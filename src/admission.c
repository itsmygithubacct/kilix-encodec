#define _GNU_SOURCE
#include "kilix_encodec_content.h"

#include <stdlib.h>

#ifndef KENC_WITH_CONTENT
kenc_result kenc_installed_assets_open(kenc_installed_assets **out,
    uint8_t profile, const char *root, uint32_t timeout_ms,
    kenc_admission_cancelled cancelled, void *context)
{
    (void)cancelled; (void)context;
    if (out == NULL) { return KENC_ERR_INVALID; }
    *out = NULL;
    if ((profile != 1u && profile != 2u) || root == NULL || root[0] != '/'
        || timeout_ms == 0u || timeout_ms > 120000u) { return KENC_ERR_INVALID; }
    return KENC_ERR_MODEL;
}
const kenc_asset_set *kenc_installed_assets_files(const kenc_installed_assets *assets)
{ (void)assets; return NULL; }
void kenc_installed_assets_free(kenc_installed_assets *assets) { free(assets); }
const char *kenc_installed_content_commit(void) { return NULL; }
const char *kenc_installed_bundle_sha256(void) { return NULL; }
#else

#include "content_bundle.h"
#include "graph_contracts.h"
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

struct kenc_installed_assets {
    kenc_asset_fd files[9];
    kenc_asset_set set;
};

static uint64_t milliseconds(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) { return UINT64_MAX; }
    return (uint64_t)now.tv_sec * 1000u + (uint64_t)now.tv_nsec / 1000000u;
}

static int stopped(uint64_t deadline, kenc_admission_cancelled cancelled, void *context)
{ return milliseconds() >= deadline || (cancelled != NULL && cancelled(context)); }

static int sealed_bundle(void)
{
    int writer = memfd_create("kenc-content-authority", MFD_CLOEXEC | MFD_ALLOW_SEALING);
    if (writer < 0) { return -1; }
    size_t offset = 0u;
    int reader = -1;
    if (fchmod(writer, 0600) != 0) { goto done; }
    while (offset < sizeof(kenc_content_bundle)) {
        ssize_t count = write(writer, kenc_content_bundle + offset, sizeof(kenc_content_bundle) - offset);
        if (count < 0 && errno == EINTR) { continue; }
        if (count <= 0) { goto done; }
        offset += (size_t)count;
    }
    if (fcntl(writer, F_ADD_SEALS, F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL) != 0) { goto done; }
    char path[64];
    (void)snprintf(path, sizeof(path), "/proc/self/fd/%d", writer);
    reader = open(path, O_RDONLY | O_CLOEXEC);
done:
    (void)close(writer);
    return reader;
}

static int safe_descriptor(int descriptor)
{
    struct stat info;
    int flags = fcntl(descriptor, F_GETFL), fdflags = fcntl(descriptor, F_GETFD);
    int seals = fcntl(descriptor, F_GET_SEALS);
    return fstat(descriptor, &info) == 0 && S_ISREG(info.st_mode)
        && info.st_uid == geteuid() && (info.st_mode & 0133) == 0
        && info.st_size > 0 && (uint64_t)info.st_size <= 128u * 1024u * 1024u
        && flags >= 0 && (flags & O_ACCMODE) == O_RDONLY
        && fdflags >= 0 && (fdflags & FD_CLOEXEC) != 0 && seals >= 0 && (seals & 15) == 15;
}

/* Always close every received right, even if ancillary structure is refused. */
static int receive_assets(int channel, pid_t process, uint8_t profile, kenc_installed_assets *assets)
{
    unsigned char bytes[13];
    union { struct cmsghdr alignment; unsigned char bytes[CMSG_SPACE(sizeof(int) * 64u)]; } control;
    struct iovec buffer = {bytes, sizeof(bytes)};
    struct msghdr message = {0};
    message.msg_iov = &buffer; message.msg_iovlen = 1u;
    message.msg_control = control.bytes; message.msg_controllen = sizeof(control.bytes);
    ssize_t count = recvmsg(channel, &message, MSG_CMSG_CLOEXEC | MSG_DONTWAIT);
    if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) { return 0; }
    if (count < 0) { return -1; }
    size_t received = 0u, records = 0u, credentials = 0u;
    int valid = count == 12 && (message.msg_flags & ~MSG_CMSG_CLOEXEC) == 0;
    for (struct cmsghdr *item = CMSG_FIRSTHDR(&message); item != NULL; item = CMSG_NXTHDR(&message, item)) {
        ++records;
        if (item->cmsg_level == SOL_SOCKET && item->cmsg_type == SCM_CREDENTIALS
            && item->cmsg_len == CMSG_LEN(sizeof(struct ucred))) {
            struct ucred identity; memcpy(&identity, CMSG_DATA(item), sizeof(identity));
            if (identity.pid != process || identity.uid != geteuid() || identity.gid != getegid()) { valid = 0; }
            ++credentials; continue;
        }
        if (item->cmsg_level != SOL_SOCKET || item->cmsg_type != SCM_RIGHTS || item->cmsg_len < CMSG_LEN(0)) {
            valid = 0; continue;
        }
        size_t size = item->cmsg_len - CMSG_LEN(0);
        if (size % sizeof(int) != 0u) { valid = 0; }
        for (size_t offset = 0u; offset + sizeof(int) <= size; offset += sizeof(int)) {
            int descriptor;
            memcpy(&descriptor, (unsigned char *)CMSG_DATA(item) + offset, sizeof(descriptor));
            if (received < 9u) { assets->files[received++].descriptor = descriptor; }
            else { (void)close(descriptor); valid = 0; }
        }
    }
    assets->set.count = received;
    size_t wanted = profile == 1u ? 9u : 4u;
    if (count != 12 || memcmp(bytes, "KCO1", 4u) != 0 || bytes[4] != profile
        || bytes[5] != wanted || bytes[6] != 0u || bytes[7] != 0u
        || bytes[8] != 0u || bytes[9] != 0u || bytes[10] != 0u || bytes[11] != 0u
        || records != 2u || credentials != 1u || received != wanted) { valid = 0; }
    for (size_t i = 0u; i < received; ++i) {
        if (!safe_descriptor(assets->files[i].descriptor)) { valid = 0; }
    }
    return valid ? 1 : -1;
}

kenc_result kenc_installed_assets_open(kenc_installed_assets **out,
    uint8_t profile, const char *root, uint32_t timeout_ms,
    kenc_admission_cancelled cancelled, void *context)
{
    if (out == NULL) { return KENC_ERR_INVALID; }
    *out = NULL;
    if ((profile != 1u && profile != 2u) || root == NULL || root[0] != '/'
        || strnlen(root, 4096u) >= 4096u || timeout_ms == 0u || timeout_ms > 120000u) { return KENC_ERR_INVALID; }
    uint64_t started = milliseconds();
    if (started == UINT64_MAX) { return KENC_ERR_RUNTIME; }
    uint64_t deadline = started + timeout_ms;
    if (stopped(deadline, cancelled, context)) { return KENC_ERR_RUNTIME; }
    /* The helper never accepts PID 1 as its parent, so a caller that is PID 1
     * of its PID namespace (a container entrypoint without an init, or
     * `unshare -pf app`) can never be answered. Say so as KENC_ERR_RUNTIME,
     * as for a deadline, before any helper starts: it is not a model refusal. */
    if (getpid() == 1) { return KENC_ERR_RUNTIME; }
    kenc_result result = KENC_ERR_MODEL;
    kenc_installed_assets *assets = calloc(1u, sizeof(*assets));
    if (assets == NULL) { return KENC_ERR_MEMORY; }
    assets->set.files = assets->files;
    int pair[2] = {-1, -1}, bundle = -1, zip_copy = -1, channel_copy = -1;
    pid_t process = -1;
    int status = 0;
    bundle = sealed_bundle();
    if (bundle < 0 || socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC | SOCK_NONBLOCK, 0, pair) != 0) { goto done; }
    int pass_credentials = 1;
    if (setsockopt(pair[0], SOL_SOCKET, SO_PASSCRED, &pass_credentials, sizeof(pass_credentials)) != 0) { goto done; }
    /* Disjoint temporary FDs prevent dup2 cycles when the caller closed stdio. */
    zip_copy = fcntl(bundle, F_DUPFD_CLOEXEC, 10);
    channel_copy = fcntl(pair[1], F_DUPFD_CLOEXEC, 10);
    if (zip_copy < 0 || channel_copy < 0) { goto done; }
    posix_spawn_file_actions_t actions;
    if (posix_spawn_file_actions_init(&actions) != 0) { goto done; }
    int error = posix_spawn_file_actions_adddup2(&actions, zip_copy, 3);
    if (error == 0) { error = posix_spawn_file_actions_adddup2(&actions, channel_copy, 4); }
    if (error == 0) { error = posix_spawn_file_actions_addopen(&actions, 0, "/dev/null", O_RDONLY, 0); }
    if (error == 0) { error = posix_spawn_file_actions_addopen(&actions, 1, "/dev/null", O_WRONLY, 0); }
    if (error == 0) { error = posix_spawn_file_actions_addopen(&actions, 2, "/dev/null", O_WRONLY, 0); }
    if (error == 0) { error = posix_spawn_file_actions_addclosefrom_np(&actions, 5); }
    char parent[32]; (void)snprintf(parent, sizeof(parent), "%ld", (long)getpid());
    char *arguments[] = {"/usr/bin/python3", "-I", "-B", "/proc/self/fd/3", parent, NULL};
    /* Only the variables kilix-license's receipt_store_root() reads cross exec,
     * so this helper finds receipts exactly where the licence screen filed them
     * ($KILIX_LICENSE_RECEIPTS, else $GPU_TERMINAL_HOME/license-receipts, else
     * $HOME/.local/gpu_terminal/license-receipts). No loader or Python injection.
     * Each crosses exactly as the caller has it, set or unset, empty included:
     * kilix-license reads an empty $HOME as "/", and dropping it made the
     * helper fall back to the passwd home while the writer used "/". */
    static const char *const receipt_names[] = {"KILIX_LICENSE_RECEIPTS", "GPU_TERMINAL_HOME", "HOME"};
    char receipt_environment[3][4128];
    char *environment[] = {"PATH=/usr/bin:/bin", "LANG=C.UTF-8", "LC_ALL=C.UTF-8", NULL, NULL, NULL, NULL};
    size_t used = 3u;
    for (size_t i = 0u; i < 3u && error == 0; ++i) {
        const char *value = getenv(receipt_names[i]);
        if (value == NULL) { continue; }
        if (strnlen(value, 4096u) >= 4096u) { error = EINVAL; continue; }
        (void)snprintf(receipt_environment[i], sizeof(receipt_environment[i]), "%s=%s", receipt_names[i], value);
        environment[used++] = receipt_environment[i];
    }
    if (error == 0) { error = posix_spawn(&process, arguments[0], &actions, NULL, arguments, environment); }
    posix_spawn_file_actions_destroy(&actions);
    (void)close(zip_copy); zip_copy = -1;
    (void)close(channel_copy); channel_copy = -1;
    (void)close(bundle); bundle = -1;
    (void)close(pair[1]); pair[1] = -1;
    if (error != 0) { process = -1; goto done; }
    unsigned char request[12u + 4096u] = {'K','C','I','1',profile,0u,0u,0u};
    for (size_t i = 0u; i < 4u; ++i) { request[8u + i] = (unsigned char)(timeout_ms >> (i * 8u)); }
    size_t bytes = strlen(root);
    memcpy(request + 12u, root, bytes);
    if (send(pair[0], request, 12u + bytes, MSG_NOSIGNAL) != (ssize_t)(12u + bytes)) { goto done; }
    int received = 0, reaped = 0;
    while (!stopped(deadline, cancelled, context)) {
        if (!received) {
            int state = receive_assets(pair[0], process, profile, assets);
            if (state < 0) { goto done; }
            received = state;
        }
        pid_t ended = waitpid(process, &status, WNOHANG);
        if (ended == process) { reaped = 1; break; }
        if (ended < 0 && errno != EINTR) { process = -1; goto done; }
        struct pollfd item = {pair[0], POLLIN, 0};
        (void)poll(&item, 1u, 5);
    }
    pid_t identity = process;
    if (reaped) { process = -1; }
    if (stopped(deadline, cancelled, context)) { result = KENC_ERR_RUNTIME; goto done; }
    if (!reaped || !WIFEXITED(status) || WEXITSTATUS(status) != 0) { goto done; }
    if (!received && receive_assets(pair[0], identity, profile, assets) != 1) { goto done; }
    /* A successful helper exits after exactly one descriptor record. Check
     * EOF through recvmsg so any trailing record or rights are refused/closed. */
    unsigned char trailing;
    union { struct cmsghdr alignment; unsigned char bytes[CMSG_SPACE(sizeof(int) * 64u)]; } controls;
    struct iovec buffer = {&trailing, 1u}; struct msghdr eof = {0};
    eof.msg_iov = &buffer; eof.msg_iovlen = 1u;
    eof.msg_control = controls.bytes; eof.msg_controllen = sizeof(controls.bytes);
    ssize_t count = recvmsg(pair[0], &eof, MSG_CMSG_CLOEXEC | MSG_DONTWAIT);
    if (count < 0) { goto done; }
    for (struct cmsghdr *item = CMSG_FIRSTHDR(&eof); item != NULL; item = CMSG_NXTHDR(&eof, item)) {
        if (item->cmsg_level == SOL_SOCKET && item->cmsg_type == SCM_RIGHTS && item->cmsg_len >= CMSG_LEN(0)) {
            size_t size = item->cmsg_len - CMSG_LEN(0);
            for (size_t i = 0u; i + sizeof(int) <= size; i += sizeof(int)) {
                int descriptor; memcpy(&descriptor, (unsigned char *)CMSG_DATA(item) + i, sizeof(descriptor));
                (void)close(descriptor);
            }
        }
    }
    if (count != 0 || eof.msg_controllen != 0u || (eof.msg_flags & ~MSG_CMSG_CLOEXEC) != 0) { goto done; }
    static const char *const stereo_names[] = {"manifest.json", "encoder_frame_op17.onnx", "decoder_frame_op17.onnx", "rvq-codebooks.f32le"};
    for (size_t i = 0u; i < assets->set.count; ++i) {
        assets->files[i].name = profile == 2u ? stereo_names[i] : i == 0u ? "manifest.json" : kenc_graphs[i-1u].file;
    }
    *out = assets; assets = NULL; result = KENC_OK;
done:
    if (process > 0) {
        (void)kill(process, SIGKILL);
        while (waitpid(process, &status, 0) < 0 && errno == EINTR) { }
    }
    if (bundle >= 0) { (void)close(bundle); }
    if (zip_copy >= 0) { (void)close(zip_copy); }
    if (channel_copy >= 0) { (void)close(channel_copy); }
    for (size_t i = 0u; i < 2u; ++i) { if (pair[i] >= 0) { (void)close(pair[i]); } }
    kenc_installed_assets_free(assets);
    return result;
}

const kenc_asset_set *kenc_installed_assets_files(const kenc_installed_assets *assets)
{ return assets == NULL ? NULL : &assets->set; }
void kenc_installed_assets_free(kenc_installed_assets *assets)
{
    if (assets == NULL) { return; }
    for (size_t i = 0u; i < assets->set.count; ++i) { (void)close(assets->files[i].descriptor); }
    free(assets);
}
const char *kenc_installed_content_commit(void) { return KENC_CONTENT_COMMIT; }
const char *kenc_installed_bundle_sha256(void) { return KENC_CONTENT_BUNDLE_SHA256; }
#endif
