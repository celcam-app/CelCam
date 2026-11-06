package app.celcam

import android.content.Context
import java.util.UUID

/**
 * Identifica a este celular ante la PC, para que siempre vuelva a la misma cámara.
 * Es un número al azar propio de CelCam: no dice nada del celular ni de quién lo usa.
 */
object PhoneId {
    private const val PREFS = "celcam_celular"
    private const val KEY = "id"

    fun get(context: Context): String {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        prefs.getString(KEY, null)?.let { return it }
        val created = UUID.randomUUID().toString()
        prefs.edit().putString(KEY, created).apply()
        return created
    }
}
