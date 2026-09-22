#define _GNU_SOURCE
#include "kilix_encodec_content.h"
#include "test.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static unsigned int passed = 0u, total = 0u;

static unsigned int descriptors(void)
{
    DIR *directory=opendir("/proc/self/fd");
    if (directory==NULL) { exit(2); }
    unsigned int count=0u;
    while (readdir(directory)!=NULL) { ++count; }
    (void)closedir(directory);
    return count;
}

static uint64_t milliseconds(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC,&now)!=0) { exit(2); }
    return (uint64_t)now.tv_sec*1000u+(uint64_t)now.tv_nsec/1000000u;
}

static int cancel_after_three(void *context)
{
    unsigned int *calls=context;
    return ++*calls>=3u;
}

int main(void)
{
    unsigned int before=descriptors();
    int unrelated=open("/dev/null",O_RDONLY|O_CLOEXEC);
    TEST_CHECK(unrelated>=0 && dup2(unrelated,60)==60);
    if (unrelated>=0) { (void)close(unrelated); }
    (void)setenv("PYTHONPATH","/invalid/ambient/content",1);
    (void)setenv("PYTHONHOME","/invalid/ambient/python",1);
    (void)setenv("PYTHONINSPECT","1",1);
    (void)setenv("LD_PRELOAD","/invalid/ambient/preload.so",1);
    (void)setenv("XDG_STATE_HOME","/invalid/ambient/state",1);
    (void)setenv("KILIX_LICENSE_RECEIPTS","/ipc/receipts",1);
    (void)setenv("GPU_TERMINAL_HOME","/ipc/stack",1);
    (void)setenv("HOME","/ipc/home",1);
    for (uint8_t profile=1u;profile<=2u;++profile) {
        kenc_installed_assets *assets=NULL;
        TEST_CHECK(kenc_installed_assets_open(&assets,profile,"/privacy",2000u,NULL,NULL)==KENC_OK);
        const kenc_asset_set *files=kenc_installed_assets_files(assets);
        TEST_CHECK(files!=NULL && files->count==(profile==1u?9u:4u));
        if (files==NULL) { exit(1); }
        for (size_t i=0u;i<files->count;++i) {
            char payload[32]={0};
            TEST_CHECK(files->files[i].name!=NULL);
            TEST_CHECK(pread(files->files[i].descriptor,payload,sizeof(payload),0)==18);
            TEST_CHECK(memcmp(payload,"fixture descriptor",18u)==0);
            TEST_CHECK(fcntl(files->files[i].descriptor,F_GETFD)==FD_CLOEXEC);
            TEST_CHECK((fcntl(files->files[i].descriptor,F_GETFL)&O_ACCMODE)==O_RDONLY);
            TEST_CHECK((fcntl(files->files[i].descriptor,F_GET_SEALS)&15)==15);
            TEST_CHECK(lseek(files->files[i].descriptor,0,SEEK_CUR)==0);
        }
        kenc_installed_assets_free(assets);
        TEST_CHECK(descriptors()==before+1u);
    }
    static const char *const refusals[]={"/few","/many","/unsealed","/writable","/wrong-profile",
        "/error","/reserved","/short","/long","/empty-first","/fdless","/extra-empty",
        "/extra-rights","/extra-data","/late-error","/exit"};
    for (size_t i=0u;i<sizeof(refusals)/sizeof(refusals[0]);++i) {
        kenc_installed_assets *assets=(kenc_installed_assets *)(uintptr_t)1u;
        TEST_CHECK(kenc_installed_assets_open(&assets,1u,refusals[i],2000u,NULL,NULL)==KENC_ERR_MODEL);
        TEST_CHECK(assets==NULL);
        TEST_CHECK(descriptors()==before+1u);
    }
    for (unsigned int mode=0u;mode<2u;++mode) {
        kenc_installed_assets *assets=NULL; unsigned int calls=0u;
        uint64_t started=milliseconds();
        TEST_CHECK(kenc_installed_assets_open(&assets,1u,"/slow",mode==0u?40u:2000u,
            mode==0u?NULL:cancel_after_three,&calls)==KENC_ERR_RUNTIME);
        TEST_CHECK(milliseconds()-started<500u);
        TEST_CHECK(assets==NULL);
        TEST_CHECK(descriptors()==before+1u);
        int status=0;
        TEST_CHECK(waitpid(-1,&status,WNOHANG)==-1 && errno==ECHILD);
    }
    TEST_CHECK(fcntl(60,F_GETFD)>=0);
    (void)close(60);
    TEST_CHECK(descriptors()==before);
    pid_t unrelated_child=fork();
    if (unrelated_child==0) { (void)sleep(5u); _exit(0); }
    TEST_CHECK(unrelated_child>0);
    if (unrelated_child>0) {
        kenc_installed_assets *assets=NULL; int status=0;
        TEST_CHECK(kenc_installed_assets_open(&assets,1u,"/slow",40u,NULL,NULL)==KENC_ERR_RUNTIME);
        TEST_CHECK(waitpid(unrelated_child,&status,WNOHANG)==0);
        TEST_CHECK(kill(unrelated_child,SIGKILL)==0);
        TEST_CHECK(waitpid(unrelated_child,&status,0)==unrelated_child);
    }
    return test_summary("admission_ipc",passed,total);
}
