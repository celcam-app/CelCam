package app.celcam

import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.media.MediaFormat
import android.os.Bundle
import android.util.Log
import android.view.Surface
import kotlin.concurrent.thread

/** Codificador H.264 por hardware. La cámara escribe en [surface]; los cuadros salen por [sink]. */
class H264Encoder(
    val width: Int,
    val height: Int,
    fps: Int,
    bitrate: Int,
    private val sink: (ByteArray, Boolean, Long) -> Unit,
) {
    private val codec = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
    val surface: Surface

    @Volatile
    private var running = true
    private val drainThread: Thread

    init {
        try {
            configure(fps, bitrate)
            surface = codec.createInputSurface()
            codec.start()
        } catch (e: RuntimeException) {
            codec.release()
            throw e
        }
        drainThread = thread(name = "celcam-codificador") { drain() }
    }

    private fun configure(fps: Int, bitrate: Int) {
        try {
            codec.configure(lowLatencyFormat(fps, bitrate), null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        } catch (e: RuntimeException) {
            // Algunos celulares rechazan las opciones de baja latencia; sin ellas igual funciona.
            Log.i(TAG, "Formato de baja latencia rechazado", e)
            codec.reset()
            codec.configure(baseFormat(fps, bitrate), null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        }
    }

    private fun baseFormat(fps: Int, bitrate: Int) =
        MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height).apply {
            setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
            setInteger(MediaFormat.KEY_BIT_RATE, bitrate)
            setInteger(MediaFormat.KEY_FRAME_RATE, fps)
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, KEYFRAME_SECONDS)
            // Con la imagen quieta, igual manda cuadros para que la PC no crea que se cortó.
            setLong(MediaFormat.KEY_REPEAT_PREVIOUS_FRAME_AFTER, REPEAT_AFTER_US)
        }

    private fun lowLatencyFormat(fps: Int, bitrate: Int) = baseFormat(fps, bitrate).apply {
        setInteger(MediaFormat.KEY_PRIORITY, 0)
        if (fps > STANDARD_FPS) setInteger(MediaFormat.KEY_OPERATING_RATE, fps)
        val capabilities = codec.codecInfo.getCapabilitiesForType(MediaFormat.MIMETYPE_VIDEO_AVC)
        // Baseline no tiene cuadros B, que agregan atraso. El nivel más alto: los bajos no alcanzan para 60 o 120.
        val baseline = capabilities.profileLevels.filter {
            it.profile == MediaCodecInfo.CodecProfileLevel.AVCProfileBaseline
        }.maxByOrNull { it.level }
        if (baseline != null) {
            setInteger(MediaFormat.KEY_PROFILE, baseline.profile)
            setInteger(MediaFormat.KEY_LEVEL, baseline.level)
        }
        val cbr = MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_CBR
        if (capabilities.encoderCapabilities.isBitrateModeSupported(cbr)) {
            setInteger(MediaFormat.KEY_BITRATE_MODE, cbr)
        }
    }

    private fun drain() {
        val info = MediaCodec.BufferInfo()
        var config = ByteArray(0)
        try {
            while (running) {
                val index = codec.dequeueOutputBuffer(info, DEQUEUE_TIMEOUT_US)
                if (index < 0) continue
                val buffer = codec.getOutputBuffer(index)
                if (buffer != null && info.size > 0) {
                    buffer.position(info.offset)
                    buffer.limit(info.offset + info.size)
                    if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0) {
                        config = ByteArray(info.size).also { buffer.get(it) }
                    } else {
                        val key = info.flags and MediaCodec.BUFFER_FLAG_KEY_FRAME != 0
                        // Cada imagen completa lleva la configuración: la PC puede engancharse en cualquiera.
                        val prefix = if (key) config else EMPTY
                        val data = ByteArray(prefix.size + info.size)
                        prefix.copyInto(data)
                        buffer.get(data, prefix.size, info.size)
                        sink(data, key, info.presentationTimeUs)
                    }
                }
                codec.releaseOutputBuffer(index, false)
            }
        } catch (e: IllegalStateException) {
            if (running) Log.w(TAG, "El codificador se detuvo", e)
        }
    }

    fun requestKeyFrame() {
        setParameter { putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0) }
    }

    fun setBitrate(bitrate: Int) {
        setParameter { putInt(MediaCodec.PARAMETER_KEY_VIDEO_BITRATE, bitrate) }
    }

    private fun setParameter(fill: Bundle.() -> Unit) {
        try {
            codec.setParameters(Bundle().apply(fill))
        } catch (e: IllegalStateException) {
            Log.i(TAG, "Parámetro ignorado: el codificador ya se detuvo", e)
        }
    }

    fun stop() {
        running = false
        drainThread.join(STOP_WAIT_MS)
        try {
            codec.stop()
        } catch (e: IllegalStateException) {
            Log.i(TAG, "stop", e)
        }
        codec.release()
        surface.release()
    }

    companion object {
        private const val TAG = "CelCam"
        private const val KEYFRAME_SECONDS = 2
        private const val REPEAT_AFTER_US = 200_000L
        private const val DEQUEUE_TIMEOUT_US = 20_000L
        private const val STOP_WAIT_MS = 1000L
        private const val STANDARD_FPS = 30
        private val EMPTY = ByteArray(0)

        /** Hasta cuántos cuadros por segundo dice aguantar el codificador con este tamaño. */
        fun maxFps(width: Int, height: Int): Int {
            val capabilities = MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos
                .firstOrNull { it.isEncoder && MediaFormat.MIMETYPE_VIDEO_AVC in it.supportedTypes }
                ?.getCapabilitiesForType(MediaFormat.MIMETYPE_VIDEO_AVC)
                ?.videoCapabilities
                ?: return Int.MAX_VALUE
            return try {
                (capabilities.getSupportedFrameRatesFor(width, height).upper + 0.5).toInt()
            } catch (_: IllegalArgumentException) {
                Int.MAX_VALUE
            }
        }
    }
}
