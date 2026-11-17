# playfair (FairPlay v3) para el receptor AirPlay de CelCam

Copia sin cambios de `lib/playfair` de [UxPlay](https://github.com/FDH2/UxPlay) (GPL-3.0, ver `LICENSE.md`),
que a su vez viene de playfair de EstebanKubata. Abre la llave que el iPhone manda cifrada con FairPlay al empezar
a duplicar su pantalla.

Lo único propio de CelCam es `celcam_playfair.c`, que expone `playfair_decrypt` para usarla desde Python
(`pc/celcam/airplay.py`). `scripts/construir.ps1` la compila con MSVC en `pc/stage/airplay/celcam_playfair.dll`.
