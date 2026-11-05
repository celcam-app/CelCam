package app.celcam

import android.content.Context
import android.net.Uri

/** Datos para encontrar y reconocer a una PC. Salen del QR o de la conexión por cable. */
data class Pairing(
    val hosts: List<String>,
    val port: Int,
    val token: String,
    val fingerprint: String,
    val pcName: String,
) {
    fun toLink(): String = Uri.Builder()
        .scheme(SCHEME)
        .authority(HOST)
        .appendQueryParameter("h", hosts.joinToString(","))
        .appendQueryParameter("p", port.toString())
        .appendQueryParameter("t", token)
        .appendQueryParameter("f", fingerprint)
        .appendQueryParameter("n", pcName)
        .build()
        .toString()

    /** Suma las direcciones ya conocidas si es la misma PC: así sirven el WiFi y el cable. */
    fun mergedWith(previous: Pairing?): Pairing {
        if (previous == null || previous.fingerprint != fingerprint) return this
        return copy(hosts = (hosts + previous.hosts).distinct().take(MAX_HOSTS))
    }

    fun save(context: Context) {
        prefs(context).edit().putString(KEY_LINK, toLink()).apply()
    }

    companion object {
        private const val SCHEME = "celcam"
        private const val HOST = "pair"
        private const val PREFS = "celcam"
        private const val KEY_LINK = "enlace"
        private const val MAX_HOSTS = 8
        private val FINGERPRINT = Regex("^[0-9a-f]{64}$")
        private val TOKEN = Regex("^[A-Za-z0-9_-]{16,64}$")

        fun parse(link: String): Pairing? {
            val uri = try {
                Uri.parse(link)
            } catch (_: RuntimeException) {
                return null
            }
            if (uri.scheme != SCHEME || uri.host != HOST || !uri.isHierarchical) return null
            // Solo direcciones de red local: un enlace malicioso no puede mandar el video a Internet.
            val hosts = uri.getQueryParameter("h")?.split(',')?.filter(::isLocalIpv4)?.take(MAX_HOSTS).orEmpty()
            val port = uri.getQueryParameter("p")?.toIntOrNull() ?: return null
            val token = uri.getQueryParameter("t") ?: return null
            val fingerprint = uri.getQueryParameter("f") ?: return null
            val name = uri.getQueryParameter("n")?.take(40).orEmpty().ifBlank { "PC" }
            if (hosts.isEmpty() || port !in 1..65535) return null
            if (!TOKEN.matches(token) || !FINGERPRINT.matches(fingerprint)) return null
            return Pairing(hosts, port, token, fingerprint, name)
        }

        fun isLocalIpv4(text: String): Boolean {
            val parts = text.split('.')
            if (parts.size != 4) return false
            val octets = parts.map { part ->
                if (part.isEmpty() || part.length > 3 || !part.all(Char::isDigit)) return false
                part.toInt().also { if (it > 255) return false }
            }
            val (a, b) = octets
            return a == 10 || a == 127 ||
                (a == 172 && b in 16..31) ||
                (a == 192 && b == 168) ||
                (a == 169 && b == 254) ||
                (a == 100 && b in 64..127)
        }

        fun load(context: Context): Pairing? =
            prefs(context).getString(KEY_LINK, null)?.let(::parse)

        fun clear(context: Context) {
            prefs(context).edit().remove(KEY_LINK).apply()
        }

        private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    }
}
