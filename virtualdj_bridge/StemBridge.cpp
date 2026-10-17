// Queries VirtualDJ's existing stems; never reads or changes the audio buffers.
#include "sdk/vdjDsp8.h"
#include <arpa/inet.h>
#include <sys/socket.h>
#include <fcntl.h>
#include <unistd.h>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>

#ifndef LED_STEM_PORT
#define LED_STEM_PORT 19785
#endif

constexpr HRESULT BRIDGE_ERROR=static_cast<HRESULT>(0x80004005L);

class StemBridge : public IVdjPluginDsp8 {
    std::atomic<bool> active_{false};
    int socket_=-1;
    sockaddr_in destination_{};
    CFRunLoopTimerRef timer_=nullptr;
    unsigned long sequence_=0;
    static void tick(CFRunLoopTimerRef, void *context) {
        static_cast<StemBridge *>(context)->publish();
    }
    double query(const char *command, bool &valid) {
        double value=0;
        if (GetInfo(command,&value)!=S_OK || !std::isfinite(value)) {
            valid=false;
            return 0;
        }
        return value;
    }
public:
    HRESULT VDJ_API OnGetPluginInfo(TVdjPluginInfo8 *info) override {
        info->PluginName="LED Stem Bridge";
        info->Author="Jonathan";
        info->Description="Read-only stem levels to localhost UDP 19785";
        info->Version="1.1";
        info->Bitmap=nullptr;
        info->Flags=0;
        return S_OK;
    }
    HRESULT VDJ_API OnLoad() override {
        socket_=socket(AF_INET,SOCK_DGRAM,0);
        if (socket_<0) return BRIDGE_ERROR;
        if (fcntl(socket_,F_SETFL,O_NONBLOCK)<0) return BRIDGE_ERROR;
        destination_.sin_family=AF_INET;
        destination_.sin_port=htons(LED_STEM_PORT);
        destination_.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
        CFRunLoopTimerContext context={0,this,nullptr,nullptr,nullptr};
        timer_=CFRunLoopTimerCreate(nullptr,CFAbsoluteTimeGetCurrent(),0.02,0,0,tick,&context);
        if (!timer_) return BRIDGE_ERROR;
        // Host queries stay on the main run loop, never a private audio worker.
        CFRunLoopAddTimer(CFRunLoopGetMain(),timer_,kCFRunLoopCommonModes);
        return S_OK;
    }
    HRESULT VDJ_API OnStart() override {
        active_.store(true);
        return S_OK;
    }
    HRESULT VDJ_API OnStop() override {
        active_.store(false);
        return S_OK;
    }
    HRESULT VDJ_API OnProcessSamples(float *, int) override {
        // Deliberately leave every sample unchanged; no analysis on audio thread.
        return S_OK;
    }
    ~StemBridge() override {
        active_.store(false);
        if (timer_) { CFRunLoopTimerInvalidate(timer_); CFRelease(timer_); }
        if (socket_>=0) close(socket_);
    }
    void publish() {
        if (!active_.load()) return;
        char packet[4096];
        bool masterValid=true;
        double master=query("get_vu_meter",masterValid);
        int used=snprintf(packet,sizeof(packet),
            "{\"version\":1,\"seq\":%lu,\"sent_at\":%.6f,\"master\":%.6f,\"decks\":[",
            sequence_++,CFAbsoluteTimeGetCurrent()+978307200.0,master);
        const char *names[]={"vocal","instru","bass","kick","hihat"};
        for (int deck=1;deck<=4;++deck) {
            bool valid=true;
            char command[128];
            auto get=[&](const char *verb) {
                snprintf(command,sizeof(command),"deck %d %s",deck,verb);
                return query(command,valid);
            };
            double playing=get("play"), volume=get("get_volume");
            double level=get("get_level"), bpm=get("get_bpm"), beat=get("get_beatpos");
            double stems[5];
            for (int i=0;i<5;++i) {
                char verb[80]; snprintf(verb,sizeof(verb),"get_level '%s'",names[i]);
                stems[i]=get(verb);
            }
            used+=snprintf(packet+used,sizeof(packet)-used,
                "%s{\"id\":%d,\"valid\":%s,\"playing\":%.0f,\"volume\":%.6f,"
                "\"level\":%.6f,\"bpm\":%.6f,\"beatpos\":%.6f,"
                "\"vocal\":%.6f,\"instru\":%.6f,\"bass\":%.6f,\"kick\":%.6f,\"hihat\":%.6f}",
                deck==1?"":",",deck,valid?"true":"false",playing,volume,level,bpm,beat,
                stems[0],stems[1],stems[2],stems[3],stems[4]);
        }
        used+=snprintf(packet+used,sizeof(packet)-used,"]}");
        if (used>0 && used<(int)sizeof(packet))
            sendto(socket_,packet,used,0,(sockaddr *)&destination_,sizeof(destination_));
    }
};

HRESULT VDJ_API DllGetClassObject(const GUID &cls,const GUID &iid,void **object) {
    if (!object) return BRIDGE_ERROR;
    *object=nullptr;
    if (memcmp(&cls,&CLSID_VdjPlugin8,sizeof(GUID)) ||
        memcmp(&iid,&IID_IVdjPluginDsp8,sizeof(GUID))) return CLASS_E_CLASSNOTAVAILABLE;
    *object=new StemBridge();
    return S_OK;
}
