# Comunicaciones Digitales - UMNG
# Pico W / Pico 2 W, MicroPython. Copiar junto con index.html a la raiz.
# Red local: no proporciona acceso a Internet. No requiere Microdot.
import network
import socket
import time
import gc
from machine import Pin

SSID = 'PicoW-AP_TRAFF'
PASSWORD = '12345678'
CHANNEL = 6
IP = '192.168.4.1'
MASK = '255.255.255.0'
HTML_FILE = 'index.html'
PORT = 80
AP_TIMEOUT_MS = 10000
REQUEST_TIMEOUT_MS = 3000
MAX_HEADER_BYTES = 4096
TRAFFIC_BYTES = 64 * 1024  # Cada descarga; no se reserva todo en RAM.
TRAFFIC_CHUNK = bytes(range(256)) * 8  # Buffer reutilizable de 2048 bytes.
TRAFFIC_TIMEOUT_MS = 8000


class HTTPError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


def send_response(conn, status='200 OK', content_type='text/plain; charset=utf-8',
                  body=b'', headers=None, head_only=False):
    if isinstance(body, str):
        body = body.encode('utf-8')
    header = ('HTTP/1.1 {}\r\nContent-Type: {}\r\nContent-Length: {}\r\n'
              'Connection: close\r\nCache-Control: no-store\r\n').format(
                  status, content_type, len(body))
    if status.startswith('204 '):
        header = header.replace('Content-Length: 0\r\n', '')
    if headers:
        for key, value in headers.items():
            header += '{}: {}\r\n'.format(key, value)
    conn.sendall((header + '\r\n').encode('utf-8'))
    if body and not head_only:
        conn.sendall(body)


def read_request(conn):
    # TCP puede entregar las cabeceras en varios fragmentos.
    data = b''
    started = time.ticks_ms()
    while b'\r\n\r\n' not in data:
        remaining = REQUEST_TIMEOUT_MS - time.ticks_diff(time.ticks_ms(), started)
        if remaining <= 0:
            raise HTTPError('408 Request Timeout', 'Tiempo de espera agotado')
        if len(data) >= MAX_HEADER_BYTES:
            raise HTTPError('431 Request Header Fields Too Large', 'Cabeceras demasiado grandes')
        conn.settimeout(remaining / 1000)
        try:
            chunk = conn.recv(min(512, MAX_HEADER_BYTES - len(data)))
        except OSError:
            raise HTTPError('408 Request Timeout', 'No se completo la peticion')
        if not chunk:
            raise HTTPError('400 Bad Request', 'Peticion incompleta')
        data += chunk
    try:
        lines = data.split(b'\r\n\r\n', 1)[0].decode('utf-8').split('\r\n')
        method, target, version = lines[0].split()
        if version not in ('HTTP/1.0', 'HTTP/1.1') or not target.startswith('/'):
            raise ValueError('Linea de peticion invalida')
        headers = {}
        for line in lines[1:]:
            key, value = line.split(':', 1)
            key = key.strip().lower()
            if not key or key in headers:
                raise ValueError('Cabecera vacia o duplicada')
            headers[key] = value.strip()
        # Los botones envian POST sin cuerpo. No se necesitan datos JSON entrantes.
        if 'transfer-encoding' in headers:
            raise HTTPError('400 Bad Request', 'Transfer-Encoding no admitido')
        length = int(headers.get('content-length', '0'))
        if length < 0:
            raise ValueError('Longitud negativa')
        if length:
            raise HTTPError('413 Content Too Large', 'Estas rutas no admiten cuerpo de peticion')
        return method, target.split('?', 1)[0]
    except (ValueError, UnicodeError):
        raise HTTPError('400 Bad Request', 'Formato de peticion invalido')


def send_traffic(conn, head_only=False):
    # Respuesta finita. La pagina solicita bloques sucesivos durante la prueba.
    header = ('HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n'
              'Content-Length: {}\r\nConnection: close\r\n'
              'Cache-Control: no-store\r\n\r\n').format(TRAFFIC_BYTES)
    conn.settimeout(1)
    conn.sendall(header.encode())
    if head_only:
        return
    started = time.ticks_ms()
    remaining = TRAFFIC_BYTES
    while remaining:
        budget = TRAFFIC_TIMEOUT_MS - time.ticks_diff(time.ticks_ms(), started)
        if budget <= 0:
            raise OSError('Tiempo de transferencia agotado')
        conn.settimeout(min(1, budget / 1000))
        size = min(remaining, len(TRAFFIC_CHUNK))
        conn.sendall(TRAFFIC_CHUNK if size == len(TRAFFIC_CHUNK) else TRAFFIC_CHUNK[:size])
        remaining -= size


def handle_request(conn, method, path, led, index_bytes):
    if path in ('/on', '/off'):
        allowed = 'POST'
    elif path in ('/', '/index.html', '/state', '/favicon.ico', '/traffic'):
        allowed = 'GET, HEAD'
    else:
        send_response(conn, '404 Not Found', body='Ruta no encontrada', head_only=method == 'HEAD')
        return
    if method not in allowed.split(', '):
        send_response(conn, '405 Method Not Allowed', body='Metodo no permitido',
                      headers={'Allow': allowed}, head_only=method == 'HEAD')
        return
    if path in ('/on', '/off'):
        led.value(1 if path == '/on' else 0)
        body = '{"ok":true,"on":' + ('true' if led.value() else 'false') + '}'
        send_response(conn, content_type='application/json', body=body)
    elif path == '/state':
        body = '{"on":' + ('true' if led.value() else 'false') + '}'
        send_response(conn, content_type='application/json', body=body, head_only=method == 'HEAD')
    elif path == '/traffic':
        send_traffic(conn, head_only=method == 'HEAD')
    elif path == '/favicon.ico':
        send_response(conn, '204 No Content')
    elif index_bytes is None:
        send_response(conn, '503 Service Unavailable', body='Falta index.html. Copielo a la raiz y reinicie.',
                      head_only=method == 'HEAD')
    else:
        send_response(conn, content_type='text/html; charset=utf-8', body=index_bytes,
                      head_only=method == 'HEAD')


def start_ap(ap):
    ap.active(False)
    time.sleep_ms(100)
    try:
        ap.config(ssid=SSID, password=PASSWORD, channel=CHANNEL)
    except TypeError:
        ap.config(essid=SSID, password=PASSWORD, channel=CHANNEL)
    ap.ifconfig((IP, MASK, IP, IP))
    ap.active(True)
    started = time.ticks_ms()
    while not ap.active():
        if time.ticks_diff(time.ticks_ms(), started) >= AP_TIMEOUT_MS:
            raise RuntimeError('El AP no se activo a tiempo')
        time.sleep_ms(50)
    print('AP:', SSID, '| IP:', ap.ifconfig()[0], '| Canal solicitado:', CHANNEL)
    print('Red local sin Internet. Abrir http://{}/'.format(ap.ifconfig()[0]))


def main():
    led = Pin('LED', Pin.OUT)
    led.value(0)
    ap = network.WLAN(network.AP_IF)
    srv = None
    try:
        start_ap(ap)
        try:
            with open(HTML_FILE, 'rb') as file:
                index_bytes = file.read()
        except OSError as error:
            print('No se pudo cargar index.html:', error)
            index_bytes = None
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('0.0.0.0', PORT))
        srv.listen(3)  # Cola de conexiones pendientes; atencion secuencial.
        print('Servidor listo. Ctrl+C para detener.')
        while True:
            conn, remote = srv.accept()
            try:
                method, path = read_request(conn)
                conn.settimeout(REQUEST_TIMEOUT_MS / 1000)
                handle_request(conn, method, path, led, index_bytes)
            except HTTPError as error:
                print(remote, error.status, error.message)
                try:
                    conn.settimeout(1)
                    send_response(conn, error.status, body=error.message)
                except OSError:
                    pass  # El cliente puede haberse desconectado.
            except OSError as error:
                # Una descarga cancelada puede haber enviado ya las cabeceras.
                # No anexar una segunda respuesta HTTP al cuerpo parcial.
                print('Conexion cerrada o transferencia interrumpida:', error)
            except Exception as error:
                print('Error atendiendo a', remote, ':', error)
                try:
                    conn.settimeout(1)
                    send_response(conn, '500 Internal Server Error', body='Error interno')
                except OSError:
                    pass
            finally:
                conn.close()
                gc.collect()
    except KeyboardInterrupt:
        print('\nServidor detenido.')
    finally:
        if srv is not None:
            srv.close()
        led.value(0)
        ap.active(False)


if __name__ == '__main__':
    main()
