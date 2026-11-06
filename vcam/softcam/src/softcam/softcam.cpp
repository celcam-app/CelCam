#include "softcam.h"

#include <olectl.h>
#include <initguid.h>

#include <softcamcore/DShowSoftcam.h>
#include <softcamcore/SenderAPI.h>


// Propios de CelCam, uno por cámara. Tienen que coincidir con CAMERA_CLSIDS de pc\celcam\vcam.py.
// {7E2A6C14-9B3D-4F58-A1C6-5D0E8B7F3A21}
DEFINE_GUID(CLSID_CelCam1,
0x7e2a6c14, 0x9b3d, 0x4f58, 0xa1, 0xc6, 0x5d, 0x0e, 0x8b, 0x7f, 0x3a, 0x21);
// {7E2A6C15-9B3D-4F58-A1C6-5D0E8B7F3A21}
DEFINE_GUID(CLSID_CelCam2,
0x7e2a6c15, 0x9b3d, 0x4f58, 0xa1, 0xc6, 0x5d, 0x0e, 0x8b, 0x7f, 0x3a, 0x21);
// {7E2A6C16-9B3D-4F58-A1C6-5D0E8B7F3A21}
DEFINE_GUID(CLSID_CelCam3,
0x7e2a6c16, 0x9b3d, 0x4f58, 0xa1, 0xc6, 0x5d, 0x0e, 0x8b, 0x7f, 0x3a, 0x21);
// {7E2A6C17-9B3D-4F58-A1C6-5D0E8B7F3A21}
DEFINE_GUID(CLSID_CelCam4,
0x7e2a6c17, 0x9b3d, 0x4f58, 0xa1, 0xc6, 0x5d, 0x0e, 0x8b, 0x7f, 0x3a, 0x21);


namespace {

// Setup data

const wchar_t* const FILTER_NAMES[] = { L"CelCam", L"CelCam 2", L"CelCam 3", L"CelCam 4" };
const GUID* const FILTER_CLASSIDS[] = { &CLSID_CelCam1, &CLSID_CelCam2, &CLSID_CelCam3, &CLSID_CelCam4 };
static_assert(sizeof(FILTER_CLASSIDS) / sizeof(FILTER_CLASSIDS[0]) == softcam::FrameBuffer::MaxCameras, "una por cámara");

const AMOVIESETUP_MEDIATYPE s_pin_types[] =
{
    {
        &MEDIATYPE_Video,       // Major type
        &MEDIASUBTYPE_NULL      // Minor type
    }
};

const AMOVIESETUP_PIN s_pins[] =
{
    {
        const_cast<LPWSTR>(L"Output"),  // Pin string name
        FALSE,                  // Is it rendered
        TRUE,                   // Is it an output
        FALSE,                  // Can we have none
        FALSE,                  // Can we have many
        &CLSID_NULL,            // Connects to filter
        NULL,                   // Connects to pin
        1,                      // Number of types
        s_pin_types             // Pin details
    }
};

const REGFILTER2 s_reg_filter2 =
{
    1,
    MERIT_DO_NOT_USE,
    1,
    s_pins
};

template <int Index>
CUnknown * WINAPI CreateSoftcamInstance(LPUNKNOWN lpunk, HRESULT *phr)
{
    return softcam::Softcam::CreateInstance(lpunk, *FILTER_CLASSIDS[Index], Index, phr);
}

} // namespace

// COM global table of objects in this dll

CFactoryTemplate g_Templates[] =
{
    { L"CelCam",   &CLSID_CelCam1, &CreateSoftcamInstance<0>, NULL, nullptr },
    { L"CelCam 2", &CLSID_CelCam2, &CreateSoftcamInstance<1>, NULL, nullptr },
    { L"CelCam 3", &CLSID_CelCam3, &CreateSoftcamInstance<2>, NULL, nullptr },
    { L"CelCam 4", &CLSID_CelCam4, &CreateSoftcamInstance<3>, NULL, nullptr },
};
int g_cTemplates = sizeof(g_Templates) / sizeof(g_Templates[0]);


STDAPI DllRegisterServer()
{
    HRESULT hr = AMovieDllRegisterServer2(TRUE);
    if (FAILED(hr))
    {
        return hr;
    }
    hr = CoInitialize(nullptr);
    if (FAILED(hr))
    {
        return hr;
    }
    do
    {
        IFilterMapper2 *pFM2 = nullptr;
        hr = CoCreateInstance(
                CLSID_FilterMapper2, nullptr, CLSCTX_INPROC_SERVER,
                IID_IFilterMapper2, (void**)&pFM2);
        if (FAILED(hr))
        {
            break;
        }
        for (int i = 0; i < softcam::FrameBuffer::MaxCameras && SUCCEEDED(hr); i++)
        {
            pFM2->UnregisterFilter(
                    &CLSID_VideoInputDeviceCategory,
                    0,
                    *FILTER_CLASSIDS[i]);
            hr = pFM2->RegisterFilter(
                    *FILTER_CLASSIDS[i],
                    FILTER_NAMES[i],
                    0,
                    &CLSID_VideoInputDeviceCategory,
                    FILTER_NAMES[i],
                    &s_reg_filter2);
        }
        pFM2->Release();
    } while (0);
    CoFreeUnusedLibraries();
    CoUninitialize();
    return hr;
}

STDAPI DllUnregisterServer()
{
    HRESULT hr = AMovieDllRegisterServer2(FALSE);
    if (FAILED(hr))
    {
        return hr;
    }
    hr = CoInitialize(nullptr);
    if (FAILED(hr))
    {
        return hr;
    }
    do
    {
        IFilterMapper2 *pFM2 = nullptr;
        hr = CoCreateInstance(
                CLSID_FilterMapper2, nullptr, CLSCTX_INPROC_SERVER,
                IID_IFilterMapper2, (void**)&pFM2);
        if (FAILED(hr))
        {
            break;
        }
        // Sigue con las demás aunque una falle: desinstalar no tiene que dejar nada.
        for (int i = 0; i < softcam::FrameBuffer::MaxCameras; i++)
        {
            HRESULT one = pFM2->UnregisterFilter(
                    &CLSID_VideoInputDeviceCategory,
                    FILTER_NAMES[i],
                    *FILTER_CLASSIDS[i]);
            if (FAILED(one))
            {
                hr = one;
            }
        }
        pFM2->Release();
    } while (0);
    CoFreeUnusedLibraries();
    CoUninitialize();
    return hr;
}

extern "C" BOOL WINAPI DllEntryPoint(HINSTANCE, ULONG, LPVOID);

BOOL APIENTRY DllMain(HANDLE hModule, DWORD  dwReason, LPVOID lpReserved)
{
    return DllEntryPoint((HINSTANCE)(hModule), dwReason, lpReserved);
}


//
// Softcam Sender API
//

extern "C" scCamera scCreateCamera(int index, int width, int height, float framerate, int channels)
{
    return softcam::sender::CreateCamera(index, width, height, framerate, channels);
}

extern "C" void     scDeleteCamera(scCamera camera)
{
    return softcam::sender::DeleteCamera(camera);
}

extern "C" void     scSendFrame(scCamera camera, const void* image_bits)
{
    return softcam::sender::SendFrame(camera, image_bits);
}

extern "C" bool     scSetFormat(scCamera camera, int width, int height, float framerate, int channels, const void* image_bits)
{
    return softcam::sender::SetFormat(camera, width, height, framerate, channels, image_bits);
}

extern "C" bool     scWaitForConnection(scCamera camera, float timeout)
{
    return softcam::sender::WaitForConnection(camera, timeout);
}

extern "C" bool     scIsConnected(scCamera camera)
{
    return softcam::sender::IsConnected(camera);
}
