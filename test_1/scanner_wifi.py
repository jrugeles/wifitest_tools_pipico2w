# WiFi scanner - MicroPython, Raspberry Pi Pico W / Pico 2 W
# Comunicaciones Digitales - UMNG
# Ejecutar con Thonny. OLED opcional: copiar ssd1306.py a la placa.
# No requiere conexion a un AP ni acceso a Internet.
import network
import time
from machine import Pin, I2C

# ---------------- CONFIGURACION ----------------
MIN_RSSI = -80             # None muestra todas las detecciones
PAUSE_SECONDS = 5          # Pausa DESPUES del escaneo; no es el periodo
TARGET_BSSID = None        # Ejemplo: "94:10:3e:aa:bb:cc"
USE_OLED = True
OLED_WIDTH = 128
OLED_HEIGHT = 32
OLED_ADDRESS = 0x3C        # Cambiar a 0x3D si lo requiere la pantalla
I2C_ID = 1
SDA_GPIO = 14
SCL_GPIO = 15
I2C_FREQUENCY = 200000


def normalize_mac(value):
    if value is None:
        return None
    compact = value.strip().replace(":", "").replace("-", "").lower()
    if len(compact) != 12 or any(c not in "0123456789abcdef" for c in compact):
        raise ValueError("TARGET_BSSID debe contener 6 bytes hexadecimales")
    return ":".join(compact[i:i + 2] for i in range(0, 12, 2))


def safe_text(text):
    # Evitar caracteres de control en consola y glifos no disponibles en OLED.
    return "".join(c if 32 <= ord(c) <= 126 else "?" for c in text)


def create_oled():
    if not USE_OLED:
        return None
    try:
        from ssd1306 import SSD1306_I2C
        bus = I2C(I2C_ID, scl=Pin(SCL_GPIO), sda=Pin(SDA_GPIO), freq=I2C_FREQUENCY)
        addresses = bus.scan()
        print("Direcciones I2C:", [hex(a) for a in addresses])
        if OLED_ADDRESS not in addresses:
            print("OLED no encontrada. Continuando por consola.")
            return None
        display = SSD1306_I2C(OLED_WIDTH, OLED_HEIGHT, bus, addr=OLED_ADDRESS)
        display.fill(0)
        display.text("WiFi scanner", 0, 0)
        display.text("Iniciando...", 0, 16)
        display.show()
        return display
    except Exception as error:
        print("OLED no disponible:", error)
        print("Continuando por consola; compruebe ssd1306.py y el cableado.")
        return None


class WiFiScanner:
    def __init__(self, min_rssi=MIN_RSSI, target_bssid=TARGET_BSSID):
        self.target_bssid = normalize_mac(target_bssid)
        self.min_rssi = min_rssi
        self.oled = None
        self.led = Pin("LED", Pin.OUT)
        self.led.off()
        self.wlan = network.WLAN(network.STA_IF)

    def start(self):
        self.wlan.active(True)
        if self.wlan.isconnected():
            self.wlan.disconnect()
        self.oled = create_oled()

    def set_min_rssi(self, rssi):
        self.min_rssi = rssi
        print("Umbral RSSI:", rssi, "(None = sin filtro)")

    def scan_networks(self):
        self.led.on()
        start = time.ticks_ms()
        try:
            raw = self.wlan.scan()
        finally:
            self.led.off()
        scan_ms = time.ticks_diff(time.ticks_ms(), start)
        networks = []
        skipped = 0
        target_seen = False
        for entry in raw:
            try:
                # Los primeros 4 campos son suficientes para el scanner.
                if len(entry) < 4:
                    raise ValueError("registro incompleto")
                ssid, bssid, channel, rssi = entry[:4]
                if len(bssid) != 6:
                    raise ValueError("BSSID invalido")
                mac = ":".join("%02x" % b for b in bssid)
                if self.target_bssid and mac != self.target_bssid:
                    continue
                if self.target_bssid:
                    target_seen = True
                if self.min_rssi is not None and rssi < self.min_rssi:
                    continue
                if isinstance(ssid, bytes):
                    ssid = ssid.decode("utf-8", "ignore")
                ssid = safe_text(str(ssid)) if ssid else "<sin SSID>"
                networks.append({
                    "ssid": ssid, "bssid": mac, "channel": channel,
                    "rssi": rssi
                })
            except (ValueError, TypeError, IndexError) as error:
                skipped += 1
                print("Registro omitido:", error)
        networks.sort(key=lambda item: item["rssi"], reverse=True)
        return networks, len(raw), skipped, scan_ms, target_seen

    def oled_message(self, line1, line2="", line3=""):
        if self.oled is None:
            return
        try:
            self.oled.fill(0)
            for index, line in enumerate((line1, line2, line3)):
                self.oled.text(safe_text(line)[:16], 0, index * 8)
            self.oled.show()
        except Exception as error:
            print("Fallo OLED; se deshabilita:", error)
            self.oled = None

    def show_on_oled(self, networks, target_seen):
        if not networks:
            if self.target_bssid:
                reason = "Fuera de umbral" if target_seen else "No detectado"
                self.oled_message("AP seleccionado", reason)
            else:
                self.oled_message("Sin AP visibles", "con este filtro")
            return
        if self.oled is None:
            return
        try:
            self.oled.fill(0)
            self.oled.text("SSID     CH  dBm", 0, 0)
            for i, item in enumerate(networks[:3]):
                line = "{:<8} {:>2} {:>4}".format(
                    item["ssid"][:8], item["channel"], item["rssi"])
                self.oled.text(line[:16], 0, 8 + 8 * i)
            self.oled.show()
        except Exception as error:
            print("Fallo OLED; se deshabilita:", error)
            self.oled = None

    def print_network_table(self, result, iteration, elapsed_ms, interval_ms):
        networks, total, skipped, scan_ms, target_seen = result
        print("\nBarrido {} | inicio t={:.3f} s | duracion={:.3f} s".format(
            iteration, elapsed_ms / 1000, scan_ms / 1000))
        if interval_ms is not None:
            print("Tiempo entre inicios: {:.3f} s".format(interval_ms / 1000))
        print("{:<20} {:>10} {:>5} {:<17}".format(
            "SSID", "RSSI (dBm)", "Canal", "BSSID"))
        print("-" * 55)
        for item in networks:
            print("{:<20} {:>10} {:>5} {:<17}".format(
                item["ssid"][:20], item["rssi"], item["channel"],
                item["bssid"]))
        print("Entradas detectadas: {} | AP tras filtros: {} | Invalidas: {}".format(
            total, len(networks), skipped))
        if self.target_bssid and not networks:
            print("AP objetivo:", "detectado, bajo el umbral" if target_seen else "no detectado")

    def close(self):
        self.oled_message("Escaneo detenido")
        try:
            self.led.off()
        finally:
            self.wlan.active(False)


def main():
    scanner = WiFiScanner()
    try:
        scanner.start()
        print("WiFi scanner - Ctrl+C para detener")
        print("Umbral:", MIN_RSSI, "| BSSID:", scanner.target_bssid or "todos")
        print("La pausa se suma a la duracion de cada barrido y su visualizacion.")
        previous_start = None
        elapsed_ms = 0
        iteration = 0
        while True:
            start = time.ticks_ms()
            interval_ms = None if previous_start is None else time.ticks_diff(start, previous_start)
            if interval_ms is not None:
                elapsed_ms += interval_ms
            previous_start = start
            iteration += 1
            try:
                result = scanner.scan_networks()
                scanner.print_network_table(result, iteration, elapsed_ms, interval_ms)
                scanner.show_on_oled(result[0], result[4])
            except OSError as error:
                print("Error de escaneo en intento {}: {}".format(iteration, error))
                scanner.oled_message("Error de escaneo", "Se reintentara")
            time.sleep(PAUSE_SECONDS)
    except KeyboardInterrupt:
        print("\nEscaneo detenido por el usuario.")
    finally:
        scanner.close()


if __name__ == "__main__":
    main()
