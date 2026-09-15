"""Descubre las IPs locales para mostrar las URLs desde las que se puede abrir
la consola en el teléfono (misma Wi-Fi)."""
import socket


def local_ips() -> list[str]:
    ips: set[str] = set()
    try:
        # Truco clásico: un socket UDP "conectado" revela la IP de salida sin enviar nada.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def console_urls(port: int) -> list[str]:
    return [f"http://localhost:{port}"] + [f"http://{ip}:{port}" for ip in local_ips()]
