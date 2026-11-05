package app.celcam

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.SurfaceTexture
import android.os.Build
import android.os.Bundle
import android.view.TextureView
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.SeekBar
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions

class MainActivity : AppCompatActivity() {
    private lateinit var preview: AspectTextureView
    private lateinit var idleGroup: View
    private lateinit var liveGroup: View
    private lateinit var dimOverlay: View
    private lateinit var idleMessage: TextView
    private lateinit var connectButton: Button
    private lateinit var statusText: TextView
    private lateinit var problemText: TextView
    private lateinit var zoomGroup: View
    private lateinit var zoomLabel: TextView
    private lateinit var zoomBar: SeekBar
    private lateinit var switchButton: Button
    private lateinit var torchButton: Button

    private var pendingConnect: Pairing? = null
    private var capture: CaptureInfo? = null

    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { result ->
        val target = pendingConnect
        pendingConnect = null
        if (result[Manifest.permission.CAMERA] == true || hasCameraPermission()) {
            target?.let(::startStreaming)
        } else {
            idleMessage.text = getString(R.string.problema_permiso)
        }
    }

    private val scanner = registerForActivityResult(ScanContract()) { result ->
        val contents = result.contents ?: return@registerForActivityResult
        val pairing = Pairing.parse(contents)
        if (pairing == null) {
            Toast.makeText(this, R.string.problema_qr, Toast.LENGTH_LONG).show()
        } else {
            accept(pairing)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        preview = findViewById(R.id.preview)
        idleGroup = findViewById(R.id.idle_group)
        liveGroup = findViewById(R.id.live_group)
        dimOverlay = findViewById(R.id.dim_overlay)
        idleMessage = findViewById(R.id.idle_message)
        connectButton = findViewById(R.id.connect_button)
        statusText = findViewById(R.id.status_text)
        problemText = findViewById(R.id.problem_text)
        zoomGroup = findViewById(R.id.zoom_group)
        zoomLabel = findViewById(R.id.zoom_label)
        zoomBar = findViewById(R.id.zoom_bar)
        switchButton = findViewById(R.id.switch_button)
        torchButton = findViewById(R.id.torch_button)

        findViewById<Button>(R.id.scan_button).setOnClickListener { scan() }
        connectButton.setOnClickListener { Pairing.load(this)?.let(::requestStart) }
        switchButton.setOnClickListener { StreamService.instance?.switchCamera() }
        torchButton.setOnClickListener { capture?.let { StreamService.instance?.setTorch(!it.torchOn) } }
        findViewById<Button>(R.id.dim_button).setOnClickListener { setDimmed(true) }
        dimOverlay.setOnClickListener { setDimmed(false) }
        findViewById<Button>(R.id.stop_button).setOnClickListener { StreamService.stop(this) }
        zoomBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(bar: SeekBar, progress: Int, fromUser: Boolean) {
                if (!fromUser) return
                val info = capture ?: return
                val value = zoomFromProgress(info, progress)
                StreamService.instance?.setZoom(value)
                showZoom(value)
            }

            override fun onStartTrackingTouch(bar: SeekBar) = Unit
            override fun onStopTrackingTouch(bar: SeekBar) = Unit
        })

        preview.surfaceTextureListener = object : TextureView.SurfaceTextureListener {
            override fun onSurfaceTextureAvailable(texture: SurfaceTexture, width: Int, height: Int) {
                StreamService.previewTexture = texture
                StreamService.instance?.setPreview(texture)
            }

            override fun onSurfaceTextureSizeChanged(texture: SurfaceTexture, width: Int, height: Int) = Unit

            override fun onSurfaceTextureDestroyed(texture: SurfaceTexture): Boolean {
                StreamService.previewTexture = null
                val service = StreamService.instance
                // Con el servicio en marcha, él la libera cuando la cámara deja de usarla.
                service?.setPreview(null)
                return service == null
            }

            override fun onSurfaceTextureUpdated(texture: SurfaceTexture) = Unit
        }

        StreamService.live.observe(this, ::render)
        handleIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        val data = intent?.dataString ?: return
        intent.data = null
        val pairing = Pairing.parse(data)
        if (pairing == null) {
            Toast.makeText(this, R.string.problema_qr, Toast.LENGTH_LONG).show()
            return
        }
        // Un enlace puede venir de cualquier app o página: una PC nueva siempre se confirma.
        if (Pairing.load(this)?.fingerprint == pairing.fingerprint) {
            accept(pairing)
        } else {
            AlertDialog.Builder(this)
                .setTitle(R.string.confirmar_titulo)
                .setMessage(getString(R.string.confirmar_texto, pairing.pcName, pairing.hosts.first()))
                .setPositiveButton(R.string.conectar) { _, _ -> accept(pairing) }
                .setNegativeButton(R.string.cancelar, null)
                .show()
        }
    }

    private fun accept(pairing: Pairing) {
        requestStart(pairing.mergedWith(Pairing.load(this)))
    }

    private fun scan() {
        scanner.launch(
            ScanOptions()
                .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                .setPrompt(getString(R.string.escaner_ayuda))
                .setBeepEnabled(false)
                .setOrientationLocked(false),
        )
    }

    private fun requestStart(pairing: Pairing) {
        val wanted = buildList {
            if (!hasCameraPermission()) add(Manifest.permission.CAMERA)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                ContextCompat.checkSelfPermission(this@MainActivity, Manifest.permission.POST_NOTIFICATIONS) !=
                PackageManager.PERMISSION_GRANTED
            ) {
                add(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
        if (wanted.isEmpty()) {
            startStreaming(pairing)
        } else {
            pendingConnect = pairing
            permissions.launch(wanted.toTypedArray())
        }
    }

    private fun startStreaming(pairing: Pairing) {
        StreamService.start(this, pairing)
    }

    private fun hasCameraPermission() =
        ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED

    private fun render(state: StreamState) {
        val active = state.phase == Phase.CONNECTING || state.phase == Phase.STREAMING
        idleGroup.isVisible = !active
        liveGroup.isVisible = active
        if (active) {
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            setDimmed(false)
        }
        statusText.text = when (state.phase) {
            Phase.STREAMING -> state.capture?.let { getString(R.string.estado_transmitiendo_fps, state.pcName, it.fps) }
                ?: getString(R.string.estado_transmitiendo, state.pcName)
            else -> getString(R.string.estado_conectando, state.pcName)
        }
        problemText.text = state.problem
        problemText.isVisible = state.problem != null

        state.capture?.takeIf { state.phase == Phase.STREAMING }.let(::renderCapture)
        if (!active) renderIdle(state)
    }

    private fun renderIdle(state: StreamState) {
        val saved = Pairing.load(this)
        connectButton.isVisible = saved != null
        saved?.let { connectButton.text = getString(R.string.conectar_a, it.pcName) }
        idleMessage.text = state.problem ?: getString(
            if (saved == null) R.string.ayuda_primera_vez else R.string.ayuda_con_pc,
        )
    }

    private fun renderCapture(info: CaptureInfo?) {
        capture = info
        if (info == null) {
            zoomGroup.isVisible = false
            return
        }
        if (info.sideways) preview.setAspect(info.height, info.width) else preview.setAspect(info.width, info.height)
        switchButton.isEnabled = info.canSwitch
        torchButton.isEnabled = info.torchAvailable
        torchButton.setText(if (info.torchOn) R.string.linterna_apagar else R.string.linterna)
        val canZoom = info.zoomMax > info.zoomMin
        zoomGroup.isVisible = canZoom
        if (canZoom) {
            zoomBar.progress = progressFromZoom(info, info.zoom)
            showZoom(info.zoom)
        }
    }

    private fun zoomFromProgress(info: CaptureInfo, progress: Int) =
        info.zoomMin + (info.zoomMax - info.zoomMin) * progress / zoomBar.max

    private fun progressFromZoom(info: CaptureInfo, zoom: Float) =
        ((zoom - info.zoomMin) / (info.zoomMax - info.zoomMin) * zoomBar.max).toInt()

    private fun showZoom(zoom: Float) {
        zoomLabel.text = getString(R.string.zoom_valor, zoom)
    }

    private fun setDimmed(dimmed: Boolean) {
        dimOverlay.isVisible = dimmed
        val attributes = window.attributes
        attributes.screenBrightness = if (dimmed) {
            DIM_BRIGHTNESS
        } else {
            WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE
        }
        window.attributes = attributes
    }

    private companion object {
        const val DIM_BRIGHTNESS = 0.01f
    }
}
