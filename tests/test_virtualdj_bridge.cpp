#include "../virtualdj_bridge/sdk/vdjDsp8.h"
#include <dlfcn.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>

struct Callbacks : IVdjCallbacks8 {
    int queries=0;
    HRESULT SendCommand(const char *) override { assert(false); return S_FALSE; }
    HRESULT GetInfo(const char *command,double *result) override {
        queries++;
        std::string query(command);
        *result = query.find("bpm")!=std::string::npos ? 120 :
                  query.find("beatpos")!=std::string::npos ? 12.5 : 0.2;
        return S_OK;
    }
    HRESULT GetStringInfo(const char *,void *,int) override { return S_FALSE; }
    HRESULT DeclareParameter(void *,int,int,const char *,const char *,float) override { return S_OK; }
    HRESULT GetSongBuffer(int,int,short **) override { assert(false); return S_FALSE; }
};
int main(int argc,char **argv) {
    assert(argc==2 || argc==3);
    int receiver=socket(AF_INET,SOCK_DGRAM,0);
    sockaddr_in address{}; address.sin_family=AF_INET;
    address.sin_port=htons(argc==3 ? std::atoi(argv[2]) : 19785); address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
    assert(bind(receiver,(sockaddr *)&address,sizeof(address))==0);
    timeval timeout{1,0}; setsockopt(receiver,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
    void *library=dlopen(argv[1],RTLD_NOW); if (!library) { std::cerr<<dlerror(); return 1; }
    using Factory=HRESULT (*)(const GUID &,const GUID &,void **);
    auto factory=(Factory)dlsym(library,"DllGetClassObject"); assert(factory);
    IVdjPluginDsp8 *plugin=nullptr;
    assert(factory(CLSID_VdjPlugin8,IID_IVdjPluginDsp8,(void **)&plugin)==S_OK);
    Callbacks callbacks; plugin->cb=&callbacks;
    TVdjPluginInfo8 info{}; assert(plugin->OnGetPluginInfo(&info)==S_OK);
    assert(plugin->OnLoad()==S_OK);
    CFRunLoopRunInMode(kCFRunLoopDefaultMode,0.04,false);
    assert(callbacks.queries==0); // Loading alone must not activate the effect.
    assert(plugin->OnStart()==S_OK);
    float audio[]={0.25f,-0.75f,1.0f,-1.0f};
    float original[4]; memcpy(original,audio,sizeof(audio));
    assert(plugin->OnProcessSamples(audio,2)==S_OK);
    assert(memcmp(original,audio,sizeof(audio))==0);
    CFRunLoopRunInMode(kCFRunLoopDefaultMode,0.06,false);
    char data[4096]; auto size=recv(receiver,data,sizeof(data)-1,0); assert(size>0); data[size]=0;
    assert(strstr(data,"\"version\":1")); assert(strstr(data,"\"bass\":0.200000"));
    assert(strstr(data,"\"id\":4")); assert(callbacks.queries>=41);
    assert(plugin->OnStop()==S_OK);
    int stopped=callbacks.queries;
    CFRunLoopRunInMode(kCFRunLoopDefaultMode,0.04,false);
    assert(callbacks.queries==stopped);
    assert(plugin->OnStart()==S_OK);
    CFRunLoopRunInMode(kCFRunLoopDefaultMode,0.04,false);
    assert(callbacks.queries>stopped);
    plugin->Release(); close(receiver); dlclose(library);
    std::cout<<"Native bridge lifecycle and UDP checks passed\n";
}
