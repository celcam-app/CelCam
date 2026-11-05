package app.celcam

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Rect
import android.graphics.SurfaceTexture
import android.hardware.camera2.CameraAccessException
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraConstrainedHighSpeedCaptureSession
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CameraMetadata
import android.hardware.camera2.CaptureRequest
import android.media.MediaCodec
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.util.Range
import android.util.Size
import android.view.Surface
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/** Lo que la pantalla necesita saber de la cámara actual. */
data class CaptureInfo(
    val width: Int,
    val height: Int,
    val sideways: Boolean,
    val torchAvailable: Boolean,
    val torchOn: Boolean,
    val zoomMin: Float,
    val zoomMax: Float,
    val zoom: Float,
    val canSwitch: Boolean,
    val fps: Int,
    val rates: String,
)

/** A qué velocidad va a funcionar la cámara. La alta velocidad es otro tipo de sesión de Android. */
private data class RatePlan(val fps: Int, val range: Range<Int>?, val highSpeed: Boolean)

/** Cámara + codificador. Todo su estado se toca en un solo hilo. */
class CaptureManager(
    context: Context,
    private val sink: (ByteArray, Boolean, Long) -> Unit,
    private val onInfo: (CaptureInfo) -> Unit,
    private val onProblem: (String?) -> Unit,
) {
    private val manager = context.getSystemService(CameraManager::class.java)
    private val thread = HandlerThread("celcam-captura").apply { start() }
    private val handler = Handler(thread.looper)
    private val cameras: List<String> = orderedCameras()

    private var config: StreamConfig? = null
    private var bitrate = 0
    private var cameraIndex = 0
    private var encoder: H264Encoder? = null
    private var device: CameraDevice? = null
    private var session: CameraCaptureSession? = null
    private var characteristics: CameraCharacteristics? = null
    private var previewTexture: SurfaceTexture? = null
    private var previewSurface: Surface? = null
    // La vista previa que forma parte de la sesión actual (puede faltar aunque exista la superficie).
    private var sessionPreview: Surface? = null
    private val retired = mutableListOf<() -> Unit>()
    private var zoom = 1f
    private var torch = false
    private var ratePlan: RatePlan? = null
    // Cámaras que dijeron admitir alta velocidad pero fallaron: siguen en la velocidad normal.
    private val highSpeedFailed = mutableSetOf<String>()
    // Sube con cada cambio: los avisos atrasados de una cámara vieja se ignoran.
    private var generation = 0

    @Volatile
    var sensorOrientation = 0
        private set

    @Volatile
    var isFront = false
        private set

    val hasCamera: Boolean get() = cameras.isNotEmpty()

    private fun orderedCameras(): List<String> {
        val ids = try {
            manager.cameraIdList.toList()
        } catch (e: CameraAccessException) {
            Log.w(TAG, "No se pudo listar las cámaras", e)
            emptyList()
        }
        // Primero la trasera: es la de mejor calidad.
        return ids.sortedBy { id ->
            when (manager.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING)) {
                CameraCharacteristics.LENS_FACING_BACK -> 0
                CameraCharacteristics.LENS_FACING_FRONT -> 1
                else -> 2
            }
        }
    }

    // Órdenes (se pueden llamar desde cualquier hilo)

    fun start(newConfig: StreamConfig, newBitrate: Int) = handler.post {
        val running = encoder
        bitrate = newBitrate
        if (running != null && config == newConfig) {
            // Reconexión con la cámara ya andando: alcanza con una imagen completa.
            running.setBitrate(newBitrate)
            running.requestKeyFrame()
            publishInfo()
        } else {
            config = newConfig
            begin()
        }
    }

    fun applyConfig(newConfig: StreamConfig, newBitrate: Int) = handler.post {
        if (config != null && config != newConfig) {
            config = newConfig
            bitrate = newBitrate
            begin()
        }
    }

    fun stop() {
        val done = CountDownLatch(1)
        handler.post {
            config = null
            shutdown()
            done.countDown()
        }
        done.await(STOP_WAIT_SECONDS, TimeUnit.SECONDS)
    }

    fun release() {
        stop()
        thread.quitSafely()
    }

    fun switchCamera() = handler.post {
        if (cameras.size > 1 && config != null) {
            cameraIndex = (cameraIndex + 1) % cameras.size
            zoom = 1f
            torch = false
            begin()
        }
    }

    fun setPreview(texture: SurfaceTexture?) = handler.post {
        val oldTexture = previewTexture
        val oldSurface = previewSurface
        previewTexture = texture
        previewSurface = null
        if (oldTexture != null) {
            // La cámara puede estar escribiendo ahí: se libera recién con la sesión nueva lista.
            retired += {
                oldSurface?.release()
                if (oldTexture !== texture) oldTexture.release()
            }
        }
        if (device != null) buildSession() else releaseRetired()
    }

    fun setZoom(value: Float) = handler.post {
        zoom = value
        updateRepeating()
    }

    fun setTorch(on: Boolean) = handler.post {
        torch = on
        updateRepeating()
        publishInfo()
    }

    fun requestKeyFrame() = handler.post { encoder?.requestKeyFrame() }

    fun setBitrate(value: Int) = handler.post {
        bitrate = value
        encoder?.setBitrate(value)
    }

    // Cámara

    @SuppressLint("MissingPermission") // El servicio no arranca sin el permiso.
    private fun begin() {
        val cfg = config ?: return
        shutdown()
        if (cameras.isEmpty()) {
            onProblem("Este celular no tiene cámara disponible.")
            return
        }
        val current = ++generation
        val id = cameras[cameraIndex]
        try {
            val chars = manager.getCameraCharacteristics(id)
            characteristics = chars
            sensorOrientation = chars.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 0
            isFront = chars.get(CameraCharacteristics.LENS_FACING) == CameraCharacteristics.LENS_FACING_FRONT
            encoder = createEncoder(id, chars, cfg)
            manager.openCamera(id, deviceCallback(current), handler)
        } catch (e: Exception) {
            when (e) {
                is CameraAccessException, is SecurityException, is IllegalArgumentException, is java.io.IOException,
                is IllegalStateException -> {
                    Log.w(TAG, "No se pudo abrir la cámara", e)
                    retryLater(current, "No se pudo abrir la cámara. Reintentando…")
                }
                else -> throw e
            }
        }
    }

    private fun createEncoder(id: String, chars: CameraCharacteristics, cfg: StreamConfig): H264Encoder {
        var lastError: Exception? = null
        for (size in candidateSizes(chars, cfg.width, cfg.height)) {
            try {
                val plan = planRate(id, chars, size, cfg.fps)
                val created = H264Encoder(size.width, size.height, plan.fps, bitrate, sink)
                ratePlan = plan
                return created
            } catch (e: RuntimeException) {
                Log.i(TAG, "El codificador no acepta $size", e)
                lastError = e
            }
        }
        throw IllegalStateException("Ningún tamaño sirvió", lastError)
    }

    private fun planRate(id: String, chars: CameraCharacteristics, size: Size, wanted: Int): RatePlan {
        val target = min(wanted, H264Encoder.maxFps(size.width, size.height))
        val normal = bestFpsRange(chars, size, target)
        val normalFps = normal?.upper ?: min(target, FALLBACK_FPS)
        if (normalFps < target && id !in highSpeedFailed) {
            highSpeedRange(chars, size, target)?.let { return RatePlan(it.upper, it, true) }
        }
        return RatePlan(normalFps, normal, false)
    }

    private fun giveUpHighSpeed() {
        Log.i(TAG, "La cámara no pudo usar la alta velocidad; sigue en la velocidad normal")
        highSpeedFailed += cameras[cameraIndex]
        begin()
    }

    private fun deviceCallback(current: Int) = object : CameraDevice.StateCallback() {
        override fun onOpened(camera: CameraDevice) {
            if (current != generation) {
                camera.close()
                return
            }
            device = camera
            onProblem(null)
            buildSession()
        }

        override fun onDisconnected(camera: CameraDevice) {
            camera.close()
            if (current == generation) {
                device = null
                retryLater(current, "Otra app está usando la cámara. Reintentando…")
            }
        }

        override fun onError(camera: CameraDevice, error: Int) {
            Log.w(TAG, "Error de cámara $error")
            camera.close()
            if (current == generation) {
                device = null
                retryLater(current, "La cámara dio un error. Reintentando…")
            }
        }
    }

    private fun retryLater(current: Int, message: String) {
        onProblem(message)
        handler.postDelayed({ if (current == generation && config != null) begin() }, RETRY_MS)
    }

    @Suppress("DEPRECATION") // La API nueva pide Android 9; la app llega hasta Android 7.
    private fun buildSession(withPreview: Boolean = true) {
        val camera = device ?: return
        val enc = encoder ?: return
        val highSpeed = ratePlan?.highSpeed == true
        session?.close()
        session = null
        val preview = if (withPreview) preparePreviewSurface(enc) else null
        val targets = listOfNotNull(enc.surface, preview)
        val current = generation
        val callback = object : CameraCaptureSession.StateCallback() {
            override fun onConfigured(newSession: CameraCaptureSession) {
                if (current != generation) return
                session = newSession
                sessionPreview = preview
                updateRepeating()
                enc.requestKeyFrame()
                releaseRetired()
                publishInfo()
            }

            override fun onConfigureFailed(failed: CameraCaptureSession) {
                if (current != generation) return
                if (highSpeed) {
                    giveUpHighSpeed()
                } else if (preview != null) {
                    // Algunos celulares no pueden dos salidas a este tamaño: sigue solo el video.
                    Log.i(TAG, "Sesión con vista previa rechazada; sigue sin ella")
                    buildSession(withPreview = false)
                } else {
                    retryLater(current, "La cámara no acepta esta calidad. Prueba otra desde la PC.")
                }
            }
        }
        try {
            if (highSpeed) {
                camera.createConstrainedHighSpeedCaptureSession(targets, callback, handler)
            } else {
                camera.createCaptureSession(targets, callback, handler)
            }
        } catch (e: CameraAccessException) {
            Log.w(TAG, "No se pudo crear la sesión", e)
            retryLater(current, "La cámara dio un error. Reintentando…")
        } catch (e: IllegalArgumentException) {
            Log.w(TAG, "No se pudo crear la sesión", e)
            if (highSpeed) giveUpHighSpeed() else retryLater(current, "La cámara dio un error. Reintentando…")
        }
    }

    private fun preparePreviewSurface(enc: H264Encoder): Surface? {
        val texture = previewTexture ?: return null
        texture.setDefaultBufferSize(enc.width, enc.height)
        return previewSurface ?: Surface(texture).also { previewSurface = it }
    }

    private fun updateRepeating() {
        val camera = device ?: return
        val active = session ?: return
        val enc = encoder ?: return
        val chars = characteristics ?: return
        try {
            val builder = camera.createCaptureRequest(CameraDevice.TEMPLATE_RECORD)
            builder.addTarget(enc.surface)
            sessionPreview?.let(builder::addTarget)
            builder.set(CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE, CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE_OFF)
            if (chars.get(CameraCharacteristics.CONTROL_AF_AVAILABLE_MODES)
                    ?.contains(CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_VIDEO) == true
            ) {
                builder.set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_VIDEO)
            }
            ratePlan?.range?.let { builder.set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, it) }
            if (torchAvailable(chars)) {
                builder.set(
                    CaptureRequest.FLASH_MODE,
                    if (torch) CaptureRequest.FLASH_MODE_TORCH else CaptureRequest.FLASH_MODE_OFF,
                )
            }
            applyZoom(builder, chars)
            if (active is CameraConstrainedHighSpeedCaptureSession) {
                active.setRepeatingBurst(active.createHighSpeedRequestList(builder.build()), null, handler)
            } else {
                active.setRepeatingRequest(builder.build(), null, handler)
            }
        } catch (e: CameraAccessException) {
            Log.w(TAG, "No se pudo actualizar la cámara", e)
        } catch (e: IllegalStateException) {
            Log.i(TAG, "Sesión cerrada mientras se actualizaba", e)
        } catch (e: IllegalArgumentException) {
            Log.w(TAG, "Pedido de cámara inválido", e)
            if (active is CameraConstrainedHighSpeedCaptureSession) giveUpHighSpeed()
        }
    }

    private fun applyZoom(builder: CaptureRequest.Builder, chars: CameraCharacteristics) {
        val (min, max) = zoomRange(chars)
        val value = zoom.coerceIn(min, max)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            builder.set(CaptureRequest.CONTROL_ZOOM_RATIO, value)
        } else {
            val area = chars.get(CameraCharacteristics.SENSOR_INFO_ACTIVE_ARRAY_SIZE) ?: return
            val cropW = (area.width() / value).toInt()
            val cropH = (area.height() / value).toInt()
            val left = (area.width() - cropW) / 2
            val top = (area.height() - cropH) / 2
            builder.set(CaptureRequest.SCALER_CROP_REGION, Rect(left, top, left + cropW, top + cropH))
        }
    }

    private fun zoomRange(chars: CameraCharacteristics): Pair<Float, Float> {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            chars.get(CameraCharacteristics.CONTROL_ZOOM_RATIO_RANGE)?.let {
                return it.lower to it.upper.coerceAtMost(MAX_ZOOM)
            }
        }
        val max = chars.get(CameraCharacteristics.SCALER_AVAILABLE_MAX_DIGITAL_ZOOM) ?: 1f
        return 1f to max.coerceAtMost(MAX_ZOOM)
    }

    private fun torchAvailable(chars: CameraCharacteristics) =
        chars.get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true

    private fun publishInfo() {
        val chars = characteristics ?: return
        val enc = encoder ?: return
        val plan = ratePlan ?: return
        val (min, max) = zoomRange(chars)
        onInfo(
            CaptureInfo(
                width = enc.width,
                height = enc.height,
                sideways = sensorOrientation % 180 != 0,
                torchAvailable = torchAvailable(chars),
                torchOn = torch,
                zoomMin = min,
                zoomMax = max,
                zoom = zoom.coerceIn(min, max),
                canSwitch = cameras.size > 1,
                fps = plan.fps,
                rates = describeRates(chars, Size(enc.width, enc.height), plan),
            ),
        )
    }

    private fun releaseRetired() {
        retired.forEach { it() }
        retired.clear()
    }

    private fun shutdown() {
        generation++
        session?.close()
        session = null
        device?.close()
        device = null
        encoder?.stop()
        encoder = null
        releaseRetired()
    }

    private companion object {
        const val TAG = "CelCam"
        const val RETRY_MS = 2000L
        const val STOP_WAIT_SECONDS = 4L
        const val MAX_ZOOM = 8f

        fun candidateSizes(chars: CameraCharacteristics, width: Int, height: Int): List<Size> {
            val map = chars.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
                ?: throw IllegalStateException("Cámara sin tamaños")
            val sizes = map.getOutputSizes(MediaCodec::class.java).orEmpty().toList()
                .ifEmpty { map.getOutputSizes(SurfaceTexture::class.java).orEmpty().toList() }
                .filter { it.width % 2 == 0 && it.height % 2 == 0 }
            val wanted = width.toFloat() / height
            val fitting = sizes.filter { it.width <= width && it.height <= height }
            // Primero la misma forma de imagen; dentro de eso, la más grande.
            val ordered = fitting.sortedWith(
                compareBy<Size> { abs(it.width.toFloat() / it.height - wanted) > ASPECT_TOLERANCE }
                    .thenByDescending { it.width * it.height },
            )
            return ordered.ifEmpty { sizes.sortedBy { it.width * it.height }.take(1) }
        }

        const val ASPECT_TOLERANCE = 0.05f
        const val FALLBACK_FPS = 30

        /** Hasta cuántos cuadros por segundo da la cámara con este tamaño, en una sesión normal. */
        fun sizeFpsLimit(chars: CameraCharacteristics, size: Size): Int {
            val map = chars.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP) ?: return Int.MAX_VALUE
            val minDuration = try {
                map.getOutputMinFrameDuration(MediaCodec::class.java, size)
            } catch (_: IllegalArgumentException) {
                0L
            }
            return if (minDuration > 0) (1_000_000_000.0 / minDuration + 0.5).toInt() else Int.MAX_VALUE
        }

        fun bestFpsRange(chars: CameraCharacteristics, size: Size, fps: Int): Range<Int>? {
            val ranges = chars.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES) ?: return null
            val limit = min(fps, sizeFpsLimit(chars, size))
            // El más alto que no se pase; a igual tope, el más fijo, para que no baje los cuadros con poca luz.
            return ranges.filter { it.upper <= limit }.maxWithOrNull(compareBy({ it.upper }, { it.lower }))
                ?: ranges.minByOrNull { max(abs(it.lower - limit), abs(it.upper - limit)) }
        }

        fun highSpeedRange(chars: CameraCharacteristics, size: Size, fps: Int): Range<Int>? {
            val capabilities = chars.get(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES) ?: return null
            if (CameraMetadata.REQUEST_AVAILABLE_CAPABILITIES_CONSTRAINED_HIGH_SPEED_VIDEO !in capabilities) return null
            val map = chars.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP) ?: return null
            if (size !in map.highSpeedVideoSizes) return null
            // Android pide un rango fijo para grabar en alta velocidad.
            return map.getHighSpeedVideoFpsRangesFor(size)
                .filter { it.lower == it.upper && it.upper <= fps }
                .maxByOrNull { it.upper }
        }

        /** Resumen para el registro de la PC: permite ver qué admite cada celular. */
        fun describeRates(chars: CameraCharacteristics, size: Size, plan: RatePlan): String {
            val ranges = chars.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES).orEmpty()
                .joinToString(" ") { "${it.lower}-${it.upper}" }
            val map = chars.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
            val high = map?.highSpeedVideoSizes.orEmpty().joinToString(" ") { sized ->
                "${sized.width}x${sized.height}:" +
                    map?.getHighSpeedVideoFpsRangesFor(sized).orEmpty().joinToString(",") { "${it.lower}-${it.upper}" }
            }
            val limit = sizeFpsLimit(chars, size).takeIf { it != Int.MAX_VALUE } ?: "?"
            val encoder = H264Encoder.maxFps(size.width, size.height).takeIf { it != Int.MAX_VALUE } ?: "?"
            return "normal $ranges | este tamaño hasta $limit | codificador hasta $encoder | " +
                "alta velocidad ${high.ifEmpty { "no" }}" + if (plan.highSpeed) " (en uso)" else ""
        }
    }
}
