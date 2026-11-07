// Original SightCircuit exec boundary. No Vulkan, socket or camera access.
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <fcntl.h>
#include <signal.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <unistd.h>
extern char **environ;
int main(int argc, char **argv) {
    if (argc < 5) return 64;
    char *end = nullptr;
    const long parent = strtol(argv[1], &end, 10);
    if (!end || *end || parent < 2) return 64;
    const long descriptor = strtol(argv[2], &end, 10);
    if (!end || *end || descriptor < 3 || descriptor > 1048576) return 64;
    const int fd = static_cast<int>(descriptor);
    // Establish containment before loading the worker or any of its libraries.
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) || prctl(PR_SET_PDEATHSIG, SIGKILL) || getppid() != parent) return 1;
    rlimit core{0, 0};
    if (setrlimit(RLIMIT_CORE, &core)) return 1;
    struct stat info{};
    unsigned char magic[4]{};
    if (fstat(fd, &info) || !S_ISREG(info.st_mode) || info.st_uid != geteuid() ||
        info.st_nlink != 1 || (info.st_mode & 06022) || !(info.st_mode & 0100) ||
        pread(fd, magic, sizeof magic, 0) != sizeof magic ||
        magic[0] != 0x7f || magic[1] != 'E' || magic[2] != 'L' || magic[3] != 'F') return 1;
    if (fcntl(fd, F_SETFD, FD_CLOEXEC) < 0) return 1;
    fexecve(fd, argv + 3, environ);
    std::fprintf(stderr, "worker_exec_failed:%d\n", errno);
    return 1;
}
