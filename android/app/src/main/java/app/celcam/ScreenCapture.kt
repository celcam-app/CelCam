package app.celcam

import android.content.Context
import android.content.Intent
import android.graphics.SurfaceTexture
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Handler
import android.os.HandlerThread
import android.util.DisplayMetrics
import android.util.Log
import android.util.Size
import android.view.Display
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * La pantalla del celular como fuente de imagen (MediaProjection): lo que se ve va al mismo codificador que la cámara.
 *
 * Android da un solo permiso por vez y una sola pantalla virtual por permiso: al girar el celular o reconectar,
 * se reusa la misma pantalla virtual con otro tamaño y otro codificador, nunca se pide de nuevo.
 */
class ScreenCapture(
    private val context: Context,
    private val resultCode: Int,
    private val resultData: Intent,
    private val sink: (ByteArray, Boolean, Long) -> Unit,
    private val onInfo: (CaptureInfo) -> Unit,
    private val onProblem: (String?) -> Unit,
    private val onEnded: () -> Unit,
) : VideoSource {
    private val thread = HandlerThread("celcam-pantalla").apply { start() }
    private val handler = Handler(thread.looper)
    private val displays = context.getSystemService(DisplayManager::class.java)

    private var projection: MediaProjection? = null
    private var display: VirtualDisplay? = null
    private var encoder: H264Encoder? = null
    private var config: StreamConfig? = null
    private var bitrate = 0
    private var size: Size? = null
    private var screen: Size? = null
    private var fps = DEFAULT_FPS

    override val upright = true
    override val sensorOrientation = 0
    override val isFront = false

    private val projectionCallback = object : MediaProjection.Callback() {
        override fun onStop() {
            // El usuario la cortó desde Android (la notificación o el aviso del sistema).
            handler.post {
                Log.i(TAG, "Android terminó la captura de pantalla")
                closeAll()
                onEnded()
            }
        }
    }

    // Al girar el celular cambia el tamaño: la imagen sigue derecha con otro codificador.
    private val checkSize = Runnable {
        if (config != null && size != null && screenSize() != screen) begin()
    }

    private val displayListener = object : DisplayManager.DisplayListener {
        override fun onDisplayChanged(displayId: Int) {
            if (displayId != Display.DEFAULT_DISPLAY) return
            // Android a veces avisa antes de tener el tamaño nuevo: se mira ahora y otra vez un poco después.
            handler.removeCallbacks(checkSize)
            Log.d(TAG, "Cambió la pantalla: ${screenSize()}")
            checkSize.run()
            for (delay in RECHECK_MS) handler.postDelayed(checkSize, delay)
        }

        override fun onDisplayAdded(displayId: Int) = Unit
        override fun onDisplayRemoved(displayId: Int) = Unit
    }

    init {
        displays.registerDisplayListener(displayListener, handler)
    }

    override fun start(config: StreamConfig, bitrate: Int) {
        handler.post {
            this.bitrate = bitrate
            val running = encoder
            if (running != null && this.config == config) {
                running.setBitrate(scaled(bitrate))
                running.requestKeyFrame()
                publishInfo()
            } else {
                this.config = config
                begin()
            }
        }
    }

    override fun applyConfig(config: StreamConfig, bitrate: Int) {
        handler.post {
            if (this.config != config) {
                this.config = config
                this.bitrate = bitrate
                begin()
            }
        }
    }

    override fun setBitrate(value: Int) {
        handler.post {
            bitrate = value
            encoder?.setBitrate(scaled(value))
        }
    }

    override fun requestKeyFrame() {
        handler.post { encoder?.requestKeyFrame() }
    }

    /** Se corta la imagen, pero el permiso queda: al reconectar no se vuelve a pedir. */
    override fun stop() {
        val done = CountDownLatch(1)
        handler.post {
            config = null
            display?.surface = null
            encoder?.stop()
            encoder = null
            size = null
            done.countDown()
        }
        done.await(STOP_WAIT_SECONDS, TimeUnit.SECONDS)
    }

    override fun release() {
        stop()
        val done = CountDownLatch(1)
        handler.post {
            closeAll()
            done.countDown()
        }
        done.await(STOP_WAIT_SECONDS, TimeUnit.SECONDS)
        displays.unregisterDisplayListener(displayListener)
        thread.quitSafely()
    }

    // Lo de la cámara no aplica a la pantalla.
    override fun setPreview(texture: SurfaceTexture?) = Unit
    override fun switchCamera() = Unit
    override fun setZoom(value: Float) { handler.post { publishInfo() } }
    override fun setTorch(on: Boolean) { handler.post { publishInfo() } }

    private fun begin() {
        val cfg = config ?: return
        val proj = projection ?: openProjection() ?: return
        val real = screenSize()
        val target = fitted(real, cfg)
        val wanted = min(cfg.fps, refreshRate())
        encoder?.stop()
        encoder = null
        try {
            fps = min(wanted, H264Encoder.maxFps(target.width, target.height))
            val created = H264Encoder(target.width, target.height, fps, scaled(bitrate), sink)
            encoder = created
            size = target
            screen = real
            val dpi = context.resources.displayMetrics.densityDpi
            val current = display
            if (current == null) {
                display = proj.createVirtualDisplay(
                    "CelCam", target.width, target.height, dpi,
                    DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, created.surface, null, handler,
                )
            } else {
                current.resize(target.width, target.height, dpi)
                current.surface = created.surface
            }
            Log.i(TAG, "Pantalla: ${real.width}x${real.height} enviada en ${target.width}x${target.height} a $fps cuadros")
            created.requestKeyFrame()
            onProblem(null)
            publishInfo()
        } catch (e: RuntimeException) {
            Log.w(TAG, "No se pudo capturar la pantalla", e)
            onProblem(context.getString(R.string.problema_pantalla))
        }
    }

    private fun openProjection(): MediaProjection? = try {
        val manager = context.getSystemService(MediaProjectionManager::class.java)
        manager.getMediaProjection(resultCode, resultData)?.also {
            // Desde Android 14 hay que registrarlo antes de crear la pantalla virtual.
            it.registerCallback(projectionCallback, handler)
            projection = it
        }
    } catch (e: RuntimeException) {
        Log.w(TAG, "Android no dio el permiso de pantalla", e)
        onProblem(context.getString(R.string.problema_pantalla))
        null
    }

    private fun closeAll() {
        display?.release()
        display = null
        encoder?.stop()
        encoder = null
        projection?.let {
            it.unregisterCallback(projectionCallback)
            it.stop()
        }
        projection = null
        config = null
        size = null
    }

    @Suppress("DEPRECATION") // getRealMetrics alcanza y existe desde Android 7.
    private fun screenSize(): Size {
        val metrics = DisplayMetrics()
        displays.getDisplay(Display.DEFAULT_DISPLAY).getRealMetrics(metrics)
        return Size(metrics.widthPixels, metrics.heightPixels)
    }

    private fun refreshRate(): Int =
        displays.getDisplay(Display.DEFAULT_DISPLAY).refreshRate.roundToInt().coerceAtLeast(DEFAULT_FPS)

    /** Lo que se transmite cabe en la calidad pedida, con la forma de la pantalla y sin agrandarla. */
    private fun fitted(real: Size, cfg: StreamConfig): Size {
        val long = max(cfg.width, cfg.height).toFloat()
        val short = min(cfg.width, cfg.height).toFloat()
        val landscape = real.width >= real.height
        val boundW = if (landscape) long else short
        val boundH = if (landscape) short else long
        val scale = min(1f, min(boundW / real.width, boundH / real.height))
        val width = max(ALIGN, (real.width * scale).toInt() / ALIGN * ALIGN)
        val height = max(ALIGN, (real.height * scale).toInt() / ALIGN * ALIGN)
        return Size(width, height)
    }

    /** Igual que en CaptureManager: la PC calcula los datos según los cuadros pedidos. */
    private fun scaled(value: Int): Int {
        val wanted = config?.fps ?: return value
        fun factor(rate: Int) = when {
            rate <= 30 -> 1f
            rate <= 60 -> 1.5f
            else -> 2f
        }
        return (value * factor(fps) / factor(wanted)).toInt()
    }

    private fun publishInfo() {
        val current = size ?: return
        onInfo(
            CaptureInfo(
                width = current.width,
                height = current.height,
                sideways = false,
                torchAvailable = false,
                torchOn = false,
                zoomMin = 1f,
                zoomMax = 1f,
                zoom = 1f,
                canSwitch = false,
                fps = fps,
                rates = "pantalla del celular",
            ),
        )
    }

    private companion object {
        const val TAG = "CelCam"
        const val DEFAULT_FPS = 30
        const val ALIGN = 16
        const val STOP_WAIT_SECONDS = 4L
        val RECHECK_MS = longArrayOf(300, 1000, 2500)
    }
}
