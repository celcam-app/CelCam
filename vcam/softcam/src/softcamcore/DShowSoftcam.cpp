#include "DShowSoftcam.h"

#include <cstring>
#include <string>
#include <algorithm>
#include <cstdio>
#include <cmath>
#include <chrono>
#include <ctime>


namespace {

//#define ENABLE_LOG
//#define LOG_FILE_PATH "C:\\my_temp\\debug_log.txt"

#if defined(ENABLE_LOG)

FILE* logfile = nullptr;
#define OPEN_LOGFILE() logfile = std::fopen(LOG_FILE_PATH, "a")
#define CLOSE_LOGFILE() [&]{ \
        if (logfile) std::fclose(logfile); \
        logfile = nullptr; \
    }()
#define NOW() []{ \
        auto tp = std::chrono::system_clock::now(); \
        auto t = std::chrono::system_clock::to_time_t(tp); \
        static char buff[128]; \
        std::strftime(buff, sizeof(buff), "%H:%M:%S", std::localtime(&t)); \
        return buff; \
    }()
std::string IID_TO_STR(REFIID riid)
{
    std::string s =
            riid == IID_IPin                ? "IPin" :
            riid == IID_IBaseFilter         ? "IBaseFilter" :
            riid == IID_IAMovieSetup        ? "IAMovieSetup" :
            riid == IID_IQualityControl     ? "IQualityControl" :
            riid == IID_IAMStreamConfig     ? "IAMStreamConfig" :
            riid == IID_IKsPropertySet      ? "IKsPropertySet" :
            riid == IID_IAMFilterMiscFlags  ? "IAMFilterMiscFlags" :
            riid == IID_IPersistPropertyBag ? "IPersistPropertyBag" :
            riid == IID_IReferenceClock     ? "IReferenceClock" :
            riid == IID_IMediaSeeking       ? "IMediaSeeking" :
            riid == IID_IAMDeviceRemoval    ? "IAMDeviceRemoval" :
            riid == IID_IAMOpenProgress     ? "IAMOpenProgress" :
            riid == IID_IMediaPosition      ? "IMediaPosition" :
            riid == IID_IMediaFilter        ? "IMediaFilter" :
            riid == IID_IBasicVideo         ? "IBasicVideo" :
            riid == IID_IBasicAudio         ? "IBasicAudio" :
            riid == IID_IVideoWindow        ? "IVideoWindow" :
            riid == IID_IUnknown            ? "IUnknown" :
            [&]() -> std::string
            {
                char buff[128];
                snprintf(buff, sizeof(buff),
                    "{%08lX-%04hX-%04hX-%02hhX%02hhX-%02hhX%02hhX%02hhX%02hhX%02hhX%02hhX}",
                    riid.Data1, riid.Data2, riid.Data3,
                    riid.Data4[0], riid.Data4[1], riid.Data4[2], riid.Data4[3],
                    riid.Data4[4], riid.Data4[5], riid.Data4[6], riid.Data4[7]);
                return buff;
            }();
    return s;
}
#define LOG(fmt, ...) [&,funcname=__func__]{ \
        if (logfile) std::fprintf(logfile, "%s: %s " fmt, NOW(), funcname, __VA_ARGS__); \
        std::printf("%s: %s " fmt, NOW(), funcname, __VA_ARGS__); \
    }()

#else // ENABLE_LOG

#define OPEN_LOGFILE()
#define CLOSE_LOGFILE()
#define LOG(...)

#endif // ENABLE_LOG


AM_MEDIA_TYPE* allocateMediaType()
{
    BYTE *pbFormat = (BYTE*)CoTaskMemAlloc(sizeof(VIDEOINFOHEADER));
    if (!pbFormat)
    {
        return nullptr;
    }

    AM_MEDIA_TYPE *amt = (AM_MEDIA_TYPE*)CoTaskMemAlloc(sizeof(AM_MEDIA_TYPE));
    if (!amt)
    {
        CoTaskMemFree(pbFormat);
        return nullptr;
    }
    std::memset(amt, 0, sizeof(*amt));
    amt->pbFormat = pbFormat;
    return amt;
}

std::size_t calcDIBSize(int width, int height)
{
    std::size_t stride = (static_cast<unsigned>(width) * 3 + 3) & ~3u;
    return stride * static_cast<unsigned>(height);
}

void fillMediaType(AM_MEDIA_TYPE* amt, int width, int height, float framerate)
{
    BYTE *pbFormat = amt->pbFormat;

    if (framerate <= 0.0f)
    {
        framerate = 30.0f;
    }
    const float bit_rate = (float)width * (float)height * 24 * framerate;
    const float period = 10 * 1000 * 1000 / framerate;

    VIDEOINFOHEADER* pFormat = (VIDEOINFOHEADER*)pbFormat;
    std::memset(pFormat, 0, sizeof(*pFormat));
    pFormat->dwBitRate = (uint32_t)(std::min)(bit_rate, (float)INT_MAX);
    pFormat->dwBitErrorRate = 0;
    pFormat->AvgTimePerFrame = (LONGLONG)std::round((std::min)(period, (float)LONG_MAX));
    pFormat->bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    pFormat->bmiHeader.biWidth = width;
    pFormat->bmiHeader.biHeight = height;
    pFormat->bmiHeader.biPlanes = 1;
    pFormat->bmiHeader.biBitCount = 24;
    pFormat->bmiHeader.biCompression = BI_RGB;
    pFormat->bmiHeader.biSizeImage = static_cast<uint32_t>(calcDIBSize(width, height));

    amt->majortype = MEDIATYPE_Video;
    amt->subtype = MEDIASUBTYPE_RGB24;
    amt->bFixedSizeSamples = TRUE;
    amt->bTemporalCompression = FALSE;
    amt->lSampleSize = static_cast<uint32_t>(calcDIBSize(width, height));
    amt->formattype = FORMAT_VideoInfo;
    amt->pUnk = nullptr;
    amt->cbFormat = sizeof(VIDEOINFOHEADER);
    amt->pbFormat = pbFormat;
}

// CelCam: tamaño que usa la cámara si una app la abre antes que el programa.
// Lo guarda el programa (celcam\vcam.py) en cada arranque.
int readRememberedSize(const wchar_t* name, int fallback)
{
    DWORD value = 0;
    DWORD size = sizeof(value);
    LSTATUS status = RegGetValueW(
            HKEY_CURRENT_USER, L"Software\\CelCam", name,
            RRF_RT_REG_DWORD, nullptr, &value, &size);
    if (status != ERROR_SUCCESS || value == 0 || value > 16384 || value % 4 != 0)
    {
        return fallback;
    }
    return static_cast<int>(value);
}

float readRememberedFramerate()
{
    DWORD value = 0;
    DWORD size = sizeof(value);
    LSTATUS status = RegGetValueW(
            HKEY_CURRENT_USER, L"Software\\CelCam", L"Cuadros",
            RRF_RT_REG_DWORD, nullptr, &value, &size);
    if (status != ERROR_SUCCESS || value == 0 || value > 240)
    {
        return 30.0f;
    }
    return static_cast<float>(value);
}

AM_MEDIA_TYPE* makeMediaType(const softcam::CameraFormat& format)
{
    AM_MEDIA_TYPE *amt = allocateMediaType();
    if (!amt)
    {
        return nullptr;
    }
    fillMediaType(amt, format.m_width, format.m_height, format.m_framerate);
    return amt;
}

// CelCam: primero el formato actual (el que usa una app que no elige), después los habituales.
// Solo dicen qué puede pedir cada app: el filtro adapta el tamaño, y los cuadros llegan cuando llegan.
std::vector<softcam::CameraFormat> makeFormats(const softcam::CameraFormat& current)
{
    const int sizes[][2] = {
        { 1920, 1080 }, { 1280, 720 }, { 960, 540 }, { 856, 480 }, { 640, 480 }, { 640, 360 }, { 320, 240 },
    };
    const float framerates[] = { 30.0f, 60.0f, 120.0f };

    std::vector<softcam::CameraFormat> formats{ current };
    for (const auto& size : sizes)
    {
        for (float framerate : framerates)
        {
            if (size[0] != current.m_width || size[1] != current.m_height || framerate != current.m_framerate)
            {
                formats.push_back(softcam::CameraFormat{ size[0], size[1], framerate });
            }
        }
    }
    return formats;
}

} //namespace

namespace softcam {


CUnknown * Softcam::CreateInstance(
                    LPUNKNOWN   lpunk,
                    const GUID& clsid,
                    HRESULT*    phr)
{
    OPEN_LOGFILE();
    LOG("===== logging started =====\n");

    return new Softcam(lpunk, clsid, phr);
}

Softcam::Softcam(LPUNKNOWN lpunk, const GUID& clsid, HRESULT *phr) :
    CSource(NAME("DirectShow Softcam"), lpunk, clsid),
    m_frame_buffer(FrameBuffer::open()),
    m_valid(true),
    m_format{
        m_frame_buffer ? m_frame_buffer.width() : readRememberedSize(L"Ancho", 1280),
        m_frame_buffer ? m_frame_buffer.height() : readRememberedSize(L"Alto", 720),
        m_frame_buffer ? m_frame_buffer.framerate() : readRememberedFramerate()
    },
    m_formats(makeFormats(m_format))
{
    // CelCam: no retener la memoria compartida mientras no se transmite. Si no, una app
    // que solo enumera cámaras impediría que el programa vuelva a crearla al reabrirse.
    m_frame_buffer.release();

    // This code is okay though it may look strange as the return value is ignored.
    // Calling the SoftcamStream constructor results in calling the CBaseOutputPin
    // constructor which registers the instance to this Softcam instance by calling
    // CSource::AddPin().
    (void)new SoftcamStream(phr, this, L"DirectShow Softcam Stream");
}


STDMETHODIMP Softcam::NonDelegatingQueryInterface(REFIID riid, __deref_out void **ppv)
{
    if (riid == IID_IAMStreamConfig)
    {
        LOG("(Softcam) IAMStreamConfig -> S_OK\n");
        return GetInterface(static_cast<IAMStreamConfig*>(this), ppv);
    }
    else
    {
        auto result = CSource::NonDelegatingQueryInterface(riid, ppv);
        LOG("(Softcam) %s -> %s\n", IID_TO_STR(riid).c_str(), result ? "(ERROR)" : "S_OK");
        return result;
    }
}

HRESULT
Softcam::SetFormat(AM_MEDIA_TYPE *mt)
{
    if (!mt)
    {
        LOG("-> E_POINTER\n");
        return E_POINTER;
    }
    if (!m_valid)
    {
        LOG("-> E_FAIL\n");
        return E_FAIL;
    }
    if (mt->majortype != MEDIATYPE_Video ||
        mt->subtype != MEDIASUBTYPE_RGB24)
    {
        LOG("-> E_FAIL (invalid media type)\n");
        return E_FAIL;
    }
    CameraFormat requested = format();
    if (mt->formattype == FORMAT_VideoInfo && mt->pbFormat)
    {
        VIDEOINFOHEADER* pFormat = (VIDEOINFOHEADER*)mt->pbFormat;
        if (!supports(pFormat->bmiHeader.biWidth, pFormat->bmiHeader.biHeight))
        {
            LOG("-> E_FAIL (invalid dimension)\n");
            return E_FAIL;
        }
        if (pFormat->bmiHeader.biBitCount != 24 ||
            pFormat->bmiHeader.biCompression != BI_RGB)
        {
            LOG("-> E_FAIL (invalid color format)\n");
            return E_FAIL;
        }
        requested.m_width = pFormat->bmiHeader.biWidth;
        requested.m_height = pFormat->bmiHeader.biHeight;
        if (pFormat->AvgTimePerFrame > 0)
        {
            requested.m_framerate = 10000000.0f / (float)pFormat->AvgTimePerFrame;
        }
    }
    const CameraFormat current = format();
    if (GetPin(0)->IsConnected() &&
        (requested.m_width != current.m_width || requested.m_height != current.m_height))
    {
        LOG("-> VFW_E_ALREADY_CONNECTED\n");
        return VFW_E_ALREADY_CONNECTED;
    }
    {
        CAutoLock lock(&m_critsec);
        m_format = requested;
    }
    LOG("-> S_OK\n");
    return S_OK;
}

HRESULT
Softcam::GetFormat(AM_MEDIA_TYPE **out_pmt)
{
    if (!out_pmt)
    {
        LOG("-> E_POINTER\n");
        return E_POINTER;
    }
    if (!m_valid)
    {
        LOG("-> E_FAIL\n");
        return E_FAIL;
    }
    AM_MEDIA_TYPE* mt = makeMediaType(format());
    if (!mt)
    {
        LOG("-> E_OUTOFMEMORY\n");
        return E_OUTOFMEMORY;
    }
    *out_pmt = mt;
    LOG("-> S_OK\n");
    return S_OK;
}

HRESULT
Softcam::GetNumberOfCapabilities(int *out_count, int *out_size)
{
    if (!out_count || !out_size)
    {
        LOG("-> E_POINTER\n");
        return E_POINTER;
    }
    if (!m_valid)
    {
        LOG("-> E_FAIL\n");
        return E_FAIL;
    }
    *out_count = (int)m_formats.size();
    *out_size = sizeof(VIDEO_STREAM_CONFIG_CAPS);
    LOG("-> S_OK\n");
    return S_OK;
}

HRESULT
Softcam::GetStreamCaps(int index, AM_MEDIA_TYPE **out_pmt, BYTE *out_scc)
{
    if (!out_pmt || !out_scc)
    {
        LOG("-> E_POINTER\n");
        return E_POINTER;
    }
    if (!m_valid)
    {
        LOG("-> E_FAIL\n");
        return E_FAIL;
    }
    if (index < 0 || index >= (int)m_formats.size())
    {
        LOG("-> S_FALSE (invalid index)\n");
        return S_FALSE;
    }
    const CameraFormat& offered = m_formats[index];
    AM_MEDIA_TYPE *mt = makeMediaType(offered);
    if (!mt)
    {
        LOG("-> E_OUTOFMEMORY\n");
        return E_OUTOFMEMORY;
    }
    *out_pmt = mt;
    std::memset(out_scc, 0, sizeof(VIDEO_STREAM_CONFIG_CAPS));
    VIDEOINFOHEADER* format = (VIDEOINFOHEADER*)mt->pbFormat;
    VIDEO_STREAM_CONFIG_CAPS* scc = (VIDEO_STREAM_CONFIG_CAPS*)out_scc;
    const SIZE size{offered.m_width, offered.m_height};
    scc->guid = FORMAT_VideoInfo;
    scc->InputSize = size;
    scc->MinCroppingSize = size;
    scc->MaxCroppingSize = size;
    scc->CropGranularityX = 1;
    scc->CropGranularityY = 1;
    scc->CropAlignX = 1;
    scc->CropAlignY = 1;
    scc->MinOutputSize = size;
    scc->MaxOutputSize = size;
    scc->OutputGranularityX = 1;
    scc->OutputGranularityY = 1;
    scc->StretchTapsX = 0;
    scc->StretchTapsY = 0;
    scc->ShrinkTapsX = 0;
    scc->ShrinkTapsY = 0;
    scc->MinFrameInterval = format->AvgTimePerFrame;
    scc->MaxFrameInterval = format->AvgTimePerFrame;
    scc->MinBitsPerSecond = (LONG)format->dwBitRate;
    scc->MaxBitsPerSecond = (LONG)format->dwBitRate;
    LOG("-> S_OK\n");
    return S_OK;
}


FrameBuffer* Softcam::getFrameBuffer()
{
    if (!m_valid)
    {
        return nullptr;
    }

    CAutoLock lock(&m_critsec);
    if (!m_frame_buffer)
    {
        // CelCam: sirve cualquier formato: la imagen se adapta al tamaño que negoció la app.
        auto fb = FrameBuffer::open();
        if (fb && fb.active())
        {
            m_frame_buffer = fb;
        }
    }
    if (m_frame_buffer)
    {
        return &m_frame_buffer;
    }
    else
    {
        return nullptr;
    }
}

void
Softcam::releaseFrameBuffer()
{
    CAutoLock lock(&m_critsec);
    m_frame_buffer.release();
}

CameraFormat
Softcam::format()
{
    CAutoLock lock(&m_critsec);
    return m_format;
}

bool
Softcam::supports(int width, int height) const
{
    return std::any_of(m_formats.begin(), m_formats.end(), [&](const CameraFormat& format)
    {
        return format.m_width == width && format.m_height == height;
    });
}

SoftcamStream::SoftcamStream(HRESULT *phr,
                         Softcam *pParent,
                         LPCWSTR pPinName) :
    CSourceStream(NAME("DirectShow Softcam Stream"), phr, pParent, pPinName),
    m_valid(pParent->valid()),
    m_width(pParent->format().m_width),
    m_height(pParent->format().m_height)
{
}


SoftcamStream::~SoftcamStream()
{
    LOG("===== logging finished =====\n");
    CLOSE_LOGFILE();
}


STDMETHODIMP SoftcamStream::NonDelegatingQueryInterface(REFIID riid, __deref_out void **ppv)
{
    if (riid == IID_IKsPropertySet )
    {
        LOG("(SoftcamStream) IKsPropertySet -> S_OK\n");
        return GetInterface(static_cast<IKsPropertySet*>(this), ppv);
    }
    else if(riid == IID_IAMStreamConfig)
    {
        LOG("(SoftcamStream) IAMStreamConfig -> S_OK\n");
        return GetInterface(static_cast<IAMStreamConfig*>(this), ppv);
    }
    else
    {
        auto result = CSourceStream::NonDelegatingQueryInterface(riid, ppv);
        LOG("(SoftcamStream) %s -> %s\n", IID_TO_STR(riid).c_str(), result ? "(ERROR)" : "S_OK");
        return result;
    }
}


HRESULT SoftcamStream::FillBuffer(IMediaSample *pms)
{
    CheckPointer(pms,E_POINTER);

    BYTE *pData;
    pms->GetPointer(&pData);
    long lDataLen = pms->GetSize();
    ZeroMemory(pData, (std::size_t)lDataLen);
    {
        if (auto fb = getParent()->getFrameBuffer())
        {
            bool active = fb->waitForNewFrame(m_frame_counter);
            fb->transferToDIB(pData, m_width, m_height, m_scale_buffer, &m_frame_counter);

            if (!active)
            {
                // The sender has deactivated this stream and stopped sending frames.
                // We release this stream and will wait a new stream to be available.
                getParent()->releaseFrameBuffer();

                // Save the last image for a placeholder.
                const std::size_t size = calcDIBSize(m_width, m_height);
                if (!m_screenshot)
                {
                    m_screenshot.reset(new uint8_t[size]);
                }
                {
                    // Darken the image to indicate that the source is inactive.
                    for (std::size_t i = 0; i < size; i++)
                    {
                        pData[i] /= 4;
                    }
                }
                std::memcpy(m_screenshot.get(), pData, size);
            }
        }
        else
        {
            // Waiting for a new stream.
            m_frame_counter = 0;
            Timer::sleep(0.100f);

            // CelCam: sin programa abierto todavía no hay imagen guardada: queda en negro.
            if (m_screenshot)
            {
                const std::size_t size = calcDIBSize(m_width, m_height);
                std::memcpy(pData, m_screenshot.get(), size);
            }
        }

        // CelCam: la hora real de cada cuadro. Con un contador fijo, un celular más lento o más
        // rápido que lo anunciado haría que el tiempo de la app se separe del real.
        CAutoLock lock(&m_critsec);
        REFERENCE_TIME start = m_sample_time;
        CRefTime now;
        const bool running = getParent()->running() && m_pFilter->StreamTime(now) == S_OK;
        if (running && now.m_time > start)
        {
            start = now.m_time;
        }
        REFERENCE_TIME stop = start + m_frame_time;
        m_sample_time = running ? start + 1 : stop;
        pms->SetTime(&start, &stop);
    }
    pms->SetSyncPoint(TRUE);
    //LOG("-> NOERROR\n");
    return NOERROR;
}


STDMETHODIMP SoftcamStream::Notify(IBaseFilter * pSender, Quality q)
{
    // CelCam: los cuadros llegan en tiempo real: no se pueden adelantar ni atrasar.
    //LOG("-> NOERROR\n");
    return NOERROR;
}


HRESULT SoftcamStream::GetMediaType(CMediaType *pmt)
{
    CheckPointer(pmt,E_POINTER);

    if (!m_valid)
    {
        LOG("-> E_FAIL\n");
        return E_FAIL;
    }

    VIDEOINFOHEADER *pvi = (VIDEOINFOHEADER*)pmt->AllocFormatBuffer(sizeof(VIDEOINFOHEADER));
    if (pvi == nullptr)
    {
        LOG("-> E_OUTOFMEMORY\n");
        return E_OUTOFMEMORY;
    }

    const CameraFormat format = getParent()->format();
    fillMediaType(pmt, format.m_width, format.m_height, format.m_framerate);

    LOG("-> NOERROR\n");
    return NOERROR;
}

HRESULT SoftcamStream::CheckMediaType(const CMediaType *pMediaType)
{
    CheckPointer(pMediaType,E_POINTER);

    if (*pMediaType->Type() != MEDIATYPE_Video ||
        *pMediaType->Subtype() != MEDIASUBTYPE_RGB24 ||
        *pMediaType->FormatType() != FORMAT_VideoInfo ||
        pMediaType->Format() == nullptr)
    {
        LOG("-> E_FAIL (invalid media type)\n");
        return E_FAIL;
    }
    const VIDEOINFOHEADER *pvi = (const VIDEOINFOHEADER*)pMediaType->Format();
    if (pvi->bmiHeader.biBitCount != 24 ||
        pvi->bmiHeader.biCompression != BI_RGB ||
        !getParent()->supports(pvi->bmiHeader.biWidth, pvi->bmiHeader.biHeight))
    {
        LOG("-> E_FAIL (invalid format)\n");
        return E_FAIL;
    }
    LOG("-> NOERROR\n");
    return NOERROR;
}

HRESULT SoftcamStream::DecideBufferSize(IMemAllocator *pAlloc,
                                        ALLOCATOR_PROPERTIES *pProperties)
{
    CheckPointer(pAlloc,E_POINTER);
    CheckPointer(pProperties,E_POINTER);

    CAutoLock lock(m_pFilter->pStateLock());
    HRESULT hr = NOERROR;

    VIDEOINFOHEADER *pvi = (VIDEOINFOHEADER *)m_mt.Format();
    pProperties->cBuffers = 1;
    pProperties->cbBuffer = (long)calcDIBSize(pvi->bmiHeader.biWidth, pvi->bmiHeader.biHeight);

    ALLOCATOR_PROPERTIES actual;
    hr = pAlloc->SetProperties(pProperties, &actual);
    if (FAILED(hr))
    {
        LOG("-> (FAILED)\n");
        return hr;
    }
    if (actual.cbBuffer < pProperties->cbBuffer)
    {
        LOG("-> E_FAIL\n");
        return E_FAIL;
    }
    LOG("-> NOERROR\n");
    return NOERROR;
}

HRESULT SoftcamStream::OnThreadCreate()
{
    CAutoLock lock(&m_critsec);
    const VIDEOINFOHEADER *pvi = (const VIDEOINFOHEADER*)m_mt.Format();
    m_width = pvi->bmiHeader.biWidth;
    m_height = pvi->bmiHeader.biHeight;
    m_frame_time = pvi->AvgTimePerFrame > 0 ? pvi->AvgTimePerFrame : 333333;
    m_sample_time = 0;
    // Una imagen guardada de otra conexión puede tener otro tamaño.
    m_screenshot.reset();

    LOG("-> NOERROR\n");
    return NOERROR;
}

HRESULT SoftcamStream::Set(REFGUID guidPropSet, DWORD dwPropID,
                           LPVOID pInstanceData, DWORD cbInstanceData,
                           LPVOID pPropData, DWORD cbPropData)
{
    LOG("-> E_NOTIMPL\n");
    return E_NOTIMPL;
}

HRESULT SoftcamStream::Get(REFGUID guidPropSet, DWORD dwPropID,
                           LPVOID pInstanceData, DWORD cbInstanceData,
                           LPVOID pPropData, DWORD cbPropData, DWORD *pcbReturned)
{
    if (guidPropSet != AMPROPSETID_Pin)
    {
        LOG("-> E_PROP_SET_UNSUPPORTED\n");
        return E_PROP_SET_UNSUPPORTED;
    }
    if (dwPropID != AMPROPERTY_PIN_CATEGORY)
    {
        LOG("-> E_PROP_ID_UNSUPPORTED\n");
        return E_PROP_ID_UNSUPPORTED;
    }
    if (pPropData == nullptr && pcbReturned == nullptr)
    {
        LOG("-> E_POINTER\n");
        return E_POINTER;
    }
    if (pcbReturned)
    {
        *pcbReturned = sizeof(GUID);
    }
    if (pPropData)
    {
        if (cbPropData < sizeof(GUID))
        {
            LOG("-> E_UNEXPECTED\n");
            return E_UNEXPECTED;
        }
        *(GUID*)pPropData = PIN_CATEGORY_CAPTURE;
    }
    LOG("-> S_OK\n");
    return S_OK;
}

HRESULT SoftcamStream::QuerySupported(REFGUID guidPropSet, DWORD dwPropID,
                              DWORD *pTypeSupport)
{
    if (guidPropSet != AMPROPSETID_Pin)
    {
        LOG("-> E_PROP_SET_UNSUPPORTED\n");
        return E_PROP_SET_UNSUPPORTED;
    }
    if (dwPropID != AMPROPERTY_PIN_CATEGORY)
    {
        LOG("-> E_PROP_ID_UNSUPPORTED\n");
        return E_PROP_ID_UNSUPPORTED;
    }
    if (pTypeSupport)
    {
        *pTypeSupport = KSPROPERTY_SUPPORT_GET;
    }
    LOG("-> S_OK\n");
    return S_OK;
}

HRESULT SoftcamStream::SetFormat(AM_MEDIA_TYPE *mt)
{
    return getParent()->SetFormat(mt);
}

HRESULT SoftcamStream::GetFormat(AM_MEDIA_TYPE **out_pmt)
{
    return getParent()->GetFormat(out_pmt);
}

HRESULT SoftcamStream::GetNumberOfCapabilities(int *out_count, int *out_size)
{
    return getParent()->GetNumberOfCapabilities(out_count, out_size);
}

HRESULT SoftcamStream::GetStreamCaps(int index, AM_MEDIA_TYPE **out_pmt, BYTE *out_scc)
{
    return getParent()->GetStreamCaps(index, out_pmt, out_scc);
}

Softcam* SoftcamStream::getParent()
{
    return static_cast<Softcam*>(m_pFilter);
}


} //namespace softcam
