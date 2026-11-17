/* CelCam: la parte de FairPlay de UxPlay (lib/playfair, GPL-3.0), sin cambios, expuesta para Python. */
#include "playfair.h"
__declspec(dllexport) void celcam_playfair_decrypt(unsigned char* message3, unsigned char* cipher_text, unsigned char* key_out)
{
    playfair_decrypt(message3, cipher_text, key_out);
}
