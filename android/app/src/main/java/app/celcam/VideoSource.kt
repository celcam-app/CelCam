package app.celcam

import android.graphics.SurfaceTexture

/** De dónde sale la imagen que se transmite: la cámara o la pantalla del celular. */
interface VideoSource {
    /** La imagen ya viene derecha (la pantalla): no hace falta girarla según cómo esté el celular. */
    val upright: Boolean
    val sensorOrientation: Int
    val isFront: Boolean

    fun start(config: StreamConfig, bitrate: Int)
    fun applyConfig(config: StreamConfig, bitrate: Int)
    fun setBitrate(value: Int)
    fun requestKeyFrame()
    fun stop()
    fun release()
    fun setPreview(texture: SurfaceTexture?)
    fun switchCamera()
    fun setZoom(value: Float)
    fun setTorch(on: Boolean)
}

/** La cámara de siempre, vista como una fuente más. */
class CameraSource(private val camera: CaptureManager) : VideoSource {
    override val upright = false
    override val sensorOrientation get() = camera.sensorOrientation
    override val isFront get() = camera.isFront

    override fun start(config: StreamConfig, bitrate: Int) { camera.start(config, bitrate) }
    override fun applyConfig(config: StreamConfig, bitrate: Int) { camera.applyConfig(config, bitrate) }
    override fun setBitrate(value: Int) { camera.setBitrate(value) }
    override fun requestKeyFrame() { camera.requestKeyFrame() }
    override fun stop() = camera.stop()
    override fun release() = camera.release()
    override fun setPreview(texture: SurfaceTexture?) { camera.setPreview(texture) }
    override fun switchCamera() { camera.switchCamera() }
    override fun setZoom(value: Float) { camera.setZoom(value) }
    override fun setTorch(on: Boolean) { camera.setTorch(on) }
}
