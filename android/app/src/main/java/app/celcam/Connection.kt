package app.celcam

import android.annotation.SuppressLint
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.IOException
import java.net.InetSocketAddress
import java.net.Socket
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.ExecutorCompletionService
import java.util.concurrent.Executors
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.X509TrustManager
import kotlin.concurrent.thread

/** La PC rechazó el código: hay que volver a escanear el QR. */
class RejectedException(reason: String) : IOException(reason)

/** La PC ya tiene un celular en cada una de sus cámaras. */
class FullException : IOException("Sin lugar en la PC")

/** Otro celular tomó la conexión con esta PC. */
class ReplacedException : IOException("Reemplazado por otro celular")

/** Responde una PC con otro certificado: no es la del QR, o reinstalaron CelCam. */
class UnknownPcException : IOException("No es la PC esperada")

data class StreamConfig(val width: Int, val height: Int, val fps: Int, val kbps: Int)

data class EncodedFrame(val data: ByteArray, val key: Boolean, val ptsUs: Long, val quarterTurns: Int)

/** Conexión TLS con la PC. El mismo protocolo que pc/celcam/protocol.py. */
class Connection private constructor(
    private val socket: Socket,
    private val plain: Socket,
    private val input: DataInputStream,
    private val output: DataOutputStream,
    val config: StreamConfig,
    val pcName: String,
    /** A cuál de las cámaras de la PC llega la imagen, empezando por 1. */
    val camera: Int,
) {
    private val writeLock = Any()

    fun sendVideo(frame: EncodedFrame) {
        synchronized(writeLock) {
            output.writeByte(TYPE_VIDEO)
            output.writeInt(VIDEO_HEADER + frame.data.size)
            output.writeByte(if (frame.key) FLAG_KEY else 0)
            output.writeByte(frame.quarterTurns)
            output.writeLong(frame.ptsUs)
            output.write(frame.data)
            output.flush()
        }
    }

    fun sendControl(command: JSONObject) {
        val raw = command.toString().toByteArray()
        synchronized(writeLock) {
            output.writeByte(TYPE_CONTROL)
            output.writeInt(raw.size)
            output.write(raw)
            output.flush()
        }
    }

    /** Espera el próximo comando de la PC. */
    fun readControl(): JSONObject {
        while (true) {
            val type = input.readUnsignedByte()
            val size = input.readInt()
            if (size !in 0..MAX_CONTROL) throw IOException("Mensaje inválido")
            val raw = ByteArray(size)
            input.readFully(raw)
            if (type == TYPE_CONTROL) return JSONObject(String(raw))
        }
    }

    /** Cerrar TLS manda un aviso por la red: no puede hacerse en el hilo principal ni trabar a quien llama. */
    fun close() {
        thread(name = "celcam-cierre", isDaemon = true) { closeQuietly(socket) }
    }

    /** Para una conexión que ya falló: destraba enseguida a quien esté enviando, sin aviso de cierre. */
    fun abort() {
        thread(name = "celcam-corte", isDaemon = true) { closeQuietly(plain) }
    }

    companion object {
        private const val PROTO = 1
        private const val TYPE_VIDEO = 1
        private const val TYPE_CONTROL = 2
        private const val FLAG_KEY = 1
        private const val VIDEO_HEADER = 10
        private const val MAX_CONTROL = 64 * 1024
        private const val CONNECT_TIMEOUT_MS = 4000
        private const val HANDSHAKE_TIMEOUT_MS = 8000
        // La PC contesta cada ping (2 s); si pasan 15 s sin nada, la conexión está muerta.
        private const val READ_TIMEOUT_MS = 15000
        private const val SEND_BUFFER = 128 * 1024

        fun connect(pairing: Pairing, device: String, appVersion: String, phoneId: String): Connection {
            val remaining = pairing.hosts.toMutableList()
            var lastError = IOException("No respondió ninguna dirección")
            var unknownPc: UnknownPcException? = null
            while (remaining.isNotEmpty()) {
                val (host, socket) = raceTcp(remaining, pairing.port) ?: break
                try {
                    return handshake(socket, host, pairing, device, appVersion, phoneId)
                } catch (e: RejectedException) {
                    throw e
                } catch (e: FullException) {
                    throw e
                } catch (e: UnknownPcException) {
                    // Una dirección vieja ahora puede ser de otra PC con CelCam: se siguen probando las demás.
                    unknownPc = e
                    remaining.remove(host)
                    closeQuietly(socket)
                } catch (e: IOException) {
                    // Por ejemplo, adb redirige el puerto pero CelCam no está abierto en esa PC.
                    lastError = e
                    remaining.remove(host)
                    closeQuietly(socket)
                }
            }
            throw unknownPc ?: lastError
        }

        /**
         * Prueba todas las direcciones a la vez, pero solo a nivel TCP. El saludo se hace una
         * sola vez: dos saludos válidos harían que la PC cierre la primera sesión.
         */
        private fun raceTcp(hosts: List<String>, port: Int): Pair<String, Socket>? {
            val pool = Executors.newFixedThreadPool(hosts.size)
            val completion = ExecutorCompletionService<Pair<String, Socket>>(pool)
            hosts.forEach { host ->
                completion.submit {
                    val socket = Socket()
                    try {
                        socket.connect(InetSocketAddress(host, port), CONNECT_TIMEOUT_MS)
                        host to socket
                    } catch (e: IOException) {
                        closeQuietly(socket)
                        throw e
                    }
                }
            }
            pool.shutdown()
            var winner: Pair<String, Socket>? = null
            var pending = hosts.size
            while (pending > 0 && winner == null) {
                pending--
                winner = try {
                    completion.take().get()
                } catch (_: Exception) {
                    null
                }
            }
            if (pending > 0) {
                val leftovers = pending
                thread(name = "celcam-sobrantes", isDaemon = true) {
                    repeat(leftovers) {
                        try {
                            closeQuietly(completion.take().get().second)
                        } catch (_: Exception) {
                        }
                    }
                }
            }
            return winner
        }

        private fun handshake(
            plain: Socket,
            host: String,
            pairing: Pairing,
            device: String,
            appVersion: String,
            phoneId: String,
        ): Connection {
            plain.tcpNoDelay = true
            plain.sendBufferSize = SEND_BUFFER
            plain.soTimeout = HANDSHAKE_TIMEOUT_MS
            val trust = PinnedTrustManager(pairing.fingerprint)
            val context = SSLContext.getInstance("TLS")
            context.init(null, arrayOf(trust), SecureRandom())
            val tls = context.socketFactory.createSocket(plain, host, pairing.port, true) as SSLSocket
            try {
                tls.startHandshake()
            } catch (e: javax.net.ssl.SSLException) {
                if (trust.mismatch) throw UnknownPcException()
                throw IOException("Falló el saludo seguro", e)
            }
            val output = DataOutputStream(BufferedOutputStream(tls.outputStream, 64 * 1024))
            val input = DataInputStream(BufferedInputStream(tls.inputStream, 16 * 1024))
            val hello = JSONObject()
                .put("token", pairing.token)
                .put("proto", PROTO)
                .put("device", device)
                .put("id", phoneId)
                .put("app", appVersion)
                .toString()
                .toByteArray()
            output.writeBytes("CCAM")
            output.writeInt(hello.size)
            output.write(hello)
            output.flush()

            val size = input.readInt()
            if (size !in 0..MAX_CONTROL) throw IOException("Respuesta inválida")
            val raw = ByteArray(size)
            input.readFully(raw)
            val reply = JSONObject(String(raw))
            if (!reply.optBoolean("ok")) {
                if (reply.optString("error") == "lleno") throw FullException()
                throw RejectedException(reply.optString("error"))
            }
            tls.soTimeout = READ_TIMEOUT_MS
            val config = StreamConfig(reply.getInt("w"), reply.getInt("h"), reply.getInt("fps"), reply.getInt("kbps"))
            val pcName = reply.optString("pc", pairing.pcName)
            return Connection(tls, plain, input, output, config, pcName, reply.optInt("camera", 1))
        }

        private fun closeQuietly(socket: Socket) {
            try {
                socket.close()
            } catch (_: IOException) {
            }
        }
    }
}

/** Solo acepta el certificado cuya huella venía en el QR. */
@SuppressLint("CustomX509TrustManager")
private class PinnedTrustManager(fingerprintHex: String) : X509TrustManager {
    private val expected = fingerprintHex.chunked(2).map { it.toInt(16).toByte() }.toByteArray()

    @Volatile
    var mismatch = false
        private set

    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        val leaf = chain?.firstOrNull() ?: throw CertificateException("Sin certificado")
        val actual = MessageDigest.getInstance("SHA-256").digest(leaf.encoded)
        if (!MessageDigest.isEqual(expected, actual)) {
            mismatch = true
            throw CertificateException("Certificado desconocido")
        }
    }

    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        throw CertificateException("No se aceptan clientes")
    }

    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
}
