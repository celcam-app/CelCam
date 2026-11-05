package app.celcam

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.SurfaceTexture
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import android.view.OrientationEventListener
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.MutableLiveData
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import kotlin.concurrent.thread
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

enum class Phase { IDLE, CONNECTING, STREAMING, REJECTED }

data class StreamState(
    val phase: Phase = Phase.IDLE,
    val pcName: String = "",
    val problem: String? = null,
    val capture: CaptureInfo? = null,
)

/** Mantiene la transmisión aunque la pantalla se apague o se cambie de app. */
class StreamService : Service() {

    private lateinit var capture: CaptureManager
    private lateinit var orientation: OrientationEventListener
    private var wakeLock: PowerManager.WakeLock? = null
    private val wifiLocks = mutableListOf<WifiManager.WifiLock>()
    private val mainHandler = Handler(Looper.getMainLooper())

    // Poco tiempo de video en cola: mejor saltear imagen que acumular atraso.
    private val queue = ArrayBlockingQueue<EncodedFrame>(QUEUE_CAPACITY)

    @Volatile
    private var queueLimit = QUEUE_CAPACITY

    // Lo que la cámara puede hacer, para avisarle a la PC. Solo el hilo de envío escribe en la red.
    private val pendingReport = AtomicReference<JSONObject?>()

    @Volatile
    private var stopping = false

    @Volatile
    private var connection: Connection? = null

    @Volatile
    private var waitingForKey = false

    @Volatile
    private var deviceDegrees = 0

    private var worker: Thread? = null

    @Volatile
    private var targetBitrate = 0

    @Volatile
    private var currentBitrate = 0

    @Volatile
    private var lastCongestion = 0L
    private var lastRaise = 0L
    private var state = StreamState()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        capture = CaptureManager(this, ::onEncoded, ::onCaptureInfo, ::onCaptureProblem)
        previewTexture?.let(capture::setPreview)
        orientation = object : OrientationEventListener(this) {
            override fun onOrientationChanged(degrees: Int) {
                if (degrees == ORIENTATION_UNKNOWN) return
                // Margen para que no salte de un lado a otro cerca de las diagonales.
                val distance = abs(((degrees - deviceDegrees + 540) % 360) - 180)
                if (distance > ROTATION_MARGIN) deviceDegrees = ((degrees + 45) / 90 * 90) % 360
            }
        }
        orientation.enable()
        instance = this
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            requestStop()
            return START_NOT_STICKY
        }
        val pairing = intent?.getStringExtra(EXTRA_LINK)?.let(Pairing::parse)
        if (pairing == null || !goForeground()) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (worker == null) {
            stopping = false
            acquireLocks()
            worker = thread(name = "celcam-sesion") { runSessions(pairing) }
        }
        return START_NOT_STICKY
    }

    private fun goForeground(): Boolean = try {
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
        } else {
            0
        }
        ServiceCompat.startForeground(this, NOTIFICATION_ID, buildNotification(), type)
        true
    } catch (e: RuntimeException) {
        // Android puede negarlo si la app no está en pantalla o falta el permiso de cámara.
        Log.w(TAG, "No se pudo iniciar en primer plano", e)
        publish(StreamState(Phase.IDLE, problem = getString(R.string.problema_primer_plano)))
        false
    }

    private fun buildNotification(): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, getString(R.string.canal_transmision), NotificationManager.IMPORTANCE_LOW),
        )
        val flags = PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), flags)
        val stop = PendingIntent.getService(
            this,
            1,
            Intent(this, StreamService::class.java).setAction(ACTION_STOP),
            flags,
        )
        return NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_videocam)
            .setContentTitle(getString(R.string.notificacion_titulo))
            .setContentText(getString(R.string.notificacion_texto))
            .setOngoing(true)
            .setContentIntent(open)
            .addAction(R.drawable.ic_stop, getString(R.string.detener), stop)
            .build()
    }

    @Suppress("DEPRECATION") // HIGH_PERF sigue siendo lo que más ayuda antes de Android 10.
    private fun acquireLocks() {
        wakeLock = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "CelCam:transmision")
            .apply { acquire() }
        val wifi = applicationContext.getSystemService(WifiManager::class.java)
        wifiLocks += wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "CelCam:wifi")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            wifiLocks += wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_LOW_LATENCY, "CelCam:wifi-baja-latencia")
        }
        wifiLocks.forEach { it.acquire() }
    }

    private fun releaseLocks() {
        wakeLock?.takeIf { it.isHeld }?.release()
        wakeLock = null
        wifiLocks.filter { it.isHeld }.forEach { it.release() }
        wifiLocks.clear()
    }

    // Sesiones con la PC

    private fun runSessions(pairing: Pairing) {
        var backoff = FIRST_RETRY_MS
        val device = "${Build.MANUFACTURER} ${Build.MODEL}".trim()
        try {
            while (!stopping) {
                publish(StreamState(Phase.CONNECTING, pairing.pcName))
                var sessionStart = 0L
                try {
                    val conn = Connection.connect(pairing, device, BuildConfig.VERSION_NAME)
                    connection = conn
                    // Se guarda recién acá: un enlace o un QR equivocado no pisa la PC que ya andaba.
                    pairing.mergedWith(Pairing.load(this)).save(this)
                    backoff = FIRST_RETRY_MS
                    sessionStart = SystemClock.elapsedRealtime()
                    try {
                        runSession(conn)
                    } finally {
                        connection = null
                        conn.close()
                        queue.clear()
                    }
                } catch (e: RejectedException) {
                    Log.i(TAG, "La PC rechazó el código: ${e.message}")
                    // Solo se olvida si era el código guardado: un enlace falso no borra el bueno.
                    if (Pairing.load(this)?.token == pairing.token) Pairing.clear(this)
                    publish(StreamState(Phase.REJECTED, pairing.pcName, getString(R.string.problema_codigo)))
                    return
                } catch (_: ReplacedException) {
                    // Sin reintentar: si no, dos celulares se quitarían la conexión sin parar.
                    publish(StreamState(Phase.IDLE, problem = getString(R.string.problema_reemplazado)))
                    return
                } catch (_: UnknownPcException) {
                    publish(StreamState(Phase.REJECTED, pairing.pcName, getString(R.string.problema_otra_pc)))
                    return
                } catch (e: IOException) {
                    Log.i(TAG, "Sin conexión con la PC: $e")
                } catch (e: JSONException) {
                    Log.w(TAG, "Respuesta inválida de la PC", e)
                }
                if (stopping) break
                // Se cortó una conexión que venía andando: se vuelve a entrar ya, con la cámara prendida,
                // para que casi no se note. Si se corta enseguida otra vez, se espera como en cualquier falla.
                if (sessionStart != 0L && SystemClock.elapsedRealtime() - sessionStart >= STABLE_SESSION_MS) continue
                capture.stop()
                publish(
                    StreamState(Phase.CONNECTING, pairing.pcName, getString(R.string.problema_reconectando)),
                )
                Thread.sleep(backoff)
                backoff = min(backoff * 2, MAX_RETRY_MS)
            }
            publish(StreamState(Phase.IDLE))
        } catch (_: InterruptedException) {
            publish(StreamState(Phase.IDLE))
        } finally {
            capture.stop()
            mainHandler.post(::finish)
        }
    }

    private fun runSession(conn: Connection) {
        targetBitrate = conn.config.kbps * 1000
        currentBitrate = targetBitrate
        lastCongestion = SystemClock.elapsedRealtime()
        // La cámara puede venir transmitiendo de la conexión anterior: la PC necesita una imagen completa.
        waitingForKey = true
        queueLimit = queueLimitFor(conn.config.fps)
        queue.clear()
        publish(StreamState(Phase.STREAMING, conn.pcName))
        capture.start(conn.config, currentBitrate)
        val sender = thread(name = "celcam-envio") { sendLoop(conn) }
        try {
            while (!stopping) handleCommand(conn, conn.readControl())
        } finally {
            sender.interrupt()
            // Si se cayó la conexión, un envío trabado demoraría la reconexión.
            if (!stopping) conn.abort()
            sender.join(STOP_WAIT_MS)
        }
    }

    private fun handleCommand(conn: Connection, command: JSONObject) {
        when (command.optString("cmd")) {
            "key" -> capture.requestKeyFrame()
            "config" -> {
                val config = StreamConfig(
                    command.getInt("w"),
                    command.getInt("h"),
                    command.getInt("fps"),
                    command.getInt("kbps"),
                )
                targetBitrate = config.kbps * 1000
                currentBitrate = targetBitrate
                capture.applyConfig(config, currentBitrate)
            }
            "bye" -> throw ReplacedException()
            "pong" -> Unit
            else -> Log.i(TAG, "Comando desconocido de ${conn.pcName}")
        }
    }

    private fun sendLoop(conn: Connection) {
        var lastPing = 0L
        try {
            while (!Thread.currentThread().isInterrupted) {
                queue.poll(POLL_MS, TimeUnit.MILLISECONDS)?.let(conn::sendVideo)
                pendingReport.getAndSet(null)?.let(conn::sendControl)
                val now = SystemClock.elapsedRealtime()
                if (now - lastPing >= PING_MS) {
                    conn.sendControl(JSONObject().put("cmd", "ping"))
                    lastPing = now
                    raiseBitrateIfStable(now)
                }
            }
        } catch (_: InterruptedException) {
        } catch (e: IOException) {
            Log.i(TAG, "Se cortó el envío: $e")
            conn.close() // Despierta al hilo que espera comandos.
        }
    }

    // Cuadros que salen del codificador

    private fun onEncoded(data: ByteArray, key: Boolean, ptsUs: Long) {
        if (waitingForKey && !key) return
        waitingForKey = false
        val frame = EncodedFrame(data, key, ptsUs, quarterTurns())
        val waiting = queue.size
        if (waiting >= queueLimit || !queue.offer(frame)) {
            // La red no da abasto: se tira lo acumulado y se sigue desde la próxima imagen completa.
            queue.clear()
            waitingForKey = true
            capture.requestKeyFrame()
            lowerBitrate()
        } else if (waiting >= queueLimit / 2 && SystemClock.elapsedRealtime() - lastCongestion >= EARLY_LOWER_MS) {
            // Mejor bajar antes de llegar a tirar cuadros: tirarlos corta la imagen y la imagen completa pesa más.
            lowerBitrate()
        }
    }

    private fun quarterTurns(): Int {
        val device = if (capture.isFront) -deviceDegrees else deviceDegrees
        return ((capture.sensorOrientation + device + 360) % 360) / 90
    }

    private fun lowerBitrate() {
        lastCongestion = SystemClock.elapsedRealtime()
        currentBitrate = max(MIN_BITRATE, currentBitrate * 7 / 10)
        capture.setBitrate(currentBitrate)
    }

    private fun raiseBitrateIfStable(now: Long) {
        if (currentBitrate >= targetBitrate) return
        if (now - lastCongestion < STABLE_MS || now - lastRaise < STABLE_MS) return
        lastRaise = now
        currentBitrate = min(targetBitrate, currentBitrate * 12 / 10)
        capture.setBitrate(currentBitrate)
    }

    // Órdenes de la pantalla

    fun setPreview(texture: SurfaceTexture?) = capture.setPreview(texture)
    fun switchCamera() = capture.switchCamera()
    fun setZoom(value: Float) = capture.setZoom(value)
    fun setTorch(on: Boolean) = capture.setTorch(on)

    @Synchronized
    private fun onCaptureInfo(info: CaptureInfo) {
        // Según los cuadros que da la cámara, no los pedidos: si no, la cola guarda mucho más tiempo de lo previsto.
        queueLimit = queueLimitFor(info.fps)
        publish(state.copy(capture = info))
        pendingReport.set(
            JSONObject()
                .put("cmd", "capture")
                .put("w", info.width)
                .put("h", info.height)
                .put("fps", info.fps)
                .put("rates", info.rates),
        )
    }

    @Synchronized
    private fun onCaptureProblem(problem: String?) {
        publish(state.copy(problem = problem))
    }

    @Synchronized
    private fun publish(newState: StreamState) {
        state = newState
        live.postValue(newState)
    }

    // Apagado

    private fun requestStop() {
        stopping = true
        connection?.close()
        val current = worker
        if (current == null) finish() else current.interrupt()
    }

    private fun finish() {
        worker = null
        releaseLocks()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        instance = null
        stopping = true
        worker?.interrupt()
        connection?.close()
        orientation.disable()
        capture.release()
        releaseLocks()
        super.onDestroy()
    }

    companion object {
        private const val TAG = "CelCam"
        private const val ACTION_STOP = "app.celcam.DETENER"
        private const val EXTRA_LINK = "enlace"
        private const val CHANNEL = "transmision"
        private const val NOTIFICATION_ID = 1
        private const val QUEUE_CAPACITY = 64
        // Unos 530 ms de video, sea cual sea la velocidad: en un Wi-Fi malo hay trabas de hasta 400 ms.
        private const val QUEUE_FRAMES_PER_15_FPS = 8
        private const val MIN_QUEUE_FRAMES = 8
        private const val EARLY_LOWER_MS = 1000L
        private const val POLL_MS = 500L
        private const val PING_MS = 2000L
        private const val STOP_WAIT_MS = 1000L
        private const val FIRST_RETRY_MS = 1000L
        private const val MAX_RETRY_MS = 5000L
        private const val STABLE_SESSION_MS = 5000L
        private const val MIN_BITRATE = 800_000
        private const val STABLE_MS = 10_000L
        private const val ROTATION_MARGIN = 55

        private fun queueLimitFor(fps: Int) =
            (fps * QUEUE_FRAMES_PER_15_FPS / 15).coerceIn(MIN_QUEUE_FRAMES, QUEUE_CAPACITY)

        val live = MutableLiveData(StreamState())

        @Volatile
        var instance: StreamService? = null
            private set

        /** La vista previa puede estar lista antes de que arranque el servicio. */
        @Volatile
        var previewTexture: SurfaceTexture? = null

        fun start(context: Context, pairing: Pairing) {
            val intent = Intent(context, StreamService::class.java).putExtra(EXTRA_LINK, pairing.toLink())
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            instance?.requestStop() ?: context.stopService(Intent(context, StreamService::class.java))
        }
    }
}
