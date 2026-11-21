import ctypes
import errno
import os
import socket
import sys

class ArgCmp(ctypes.Structure):
    _fields_ = [("arg", ctypes.c_uint), ("op", ctypes.c_uint),
                ("datum_a", ctypes.c_uint64), ("datum_b", ctypes.c_uint64)]

sec = ctypes.CDLL("libseccomp.so.2")
sec.seccomp_init.argtypes = [ctypes.c_uint32]
sec.seccomp_init.restype = ctypes.c_void_p
sec.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
sec.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                     ctypes.c_uint, ctypes.POINTER(ArgCmp)]
sec.seccomp_load.argtypes = [ctypes.c_void_p]
ctx = sec.seccomp_init(0x7FFF0000)
assert ctx
deny = 0x00050000 | errno.EPERM
for name in (b"socket", b"socketpair"):
    for domain in (socket.AF_INET, socket.AF_INET6, socket.AF_PACKET):
        comparison = ArgCmp(0, 4, domain, 0)
        assert sec.seccomp_rule_add_array(ctx, deny, sec.seccomp_syscall_resolve_name(name),
                                          1, ctypes.byref(comparison)) == 0
assert sec.seccomp_load(ctx) == 0
for domain in (socket.AF_INET, socket.AF_INET6):
    try:
        socket.socket(domain, socket.SOCK_STREAM)
    except PermissionError:
        pass
    else:
        raise RuntimeError("Network isolation failed")
os.execvp(sys.argv[1], sys.argv[1:])
