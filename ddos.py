
import csv
import time
import socket
import ipaddress
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests
import matplotlib.pyplot as plt


# ==================================================
# KONFIGURASI
# ==================================================

MAX_DURATION = 5000
MAX_RPS = 20
MAX_WORKERS = 20
REQUEST_TIMEOUT = 5

# Tambahkan hostname/IP server milikmu atau yang
# sudah mendapatkan izin pengujian.
# Contoh: "contoh-domain.com"
AUTHORIZED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "stieieu.ecampuz.com",
    # "contoh-domain.com",
}

LOG_DIR = "logs"
REPORT_DIR = "reports"

log_lock = threading.Lock()
results = []


# ==================================================
# LOG TERMINAL
# ==================================================

def log(message, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with log_lock:
        print(f"[{timestamp}] [{level}] {message}", flush=True)


# ==================================================
# VALIDASI TARGET
# ==================================================

def validate_url(url):
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL harus menggunakan http:// atau https://")

    if not parsed.hostname:
        raise ValueError("Hostname URL tidak valid.")

    if parsed.username or parsed.password:
        raise ValueError("URL tidak boleh berisi username/password.")

    hostname = parsed.hostname.lower()

    if hostname not in AUTHORIZED_HOSTS:
        raise ValueError(
            f"Target '{hostname}' belum ada di AUTHORIZED_HOSTS."
        )

    try:
        addresses = socket.getaddrinfo(
            hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        )
    except socket.gaierror:
        raise ValueError("Hostname tidak dapat ditemukan.")

    # Pastikan alamat IP hasil DNS tidak berubah menjadi
    # alamat lokal/private yang tidak diizinkan atau sebaliknya.
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])

        if hostname not in ("localhost", "127.0.0.1", "::1"):
            if ip.is_loopback or ip.is_multicast or ip.is_unspecified:
                raise ValueError("Alamat IP target tidak diizinkan.")

    return url


# ==================================================
# KIRIM REQUEST
# ==================================================

def send_request(url, request_id):
    start = time.perf_counter()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"}
        )

        latency = round((time.perf_counter() - start) * 1000, 2)

        result = {
            "id": request_id,
            "time": timestamp,
            "status": response.status_code,
            "latency_ms": latency,
            "error": ""
        }

        if response.status_code == 200:
            log(
                f"Request #{request_id} berhasil | "
                f"HTTP {response.status_code} | {latency} ms",
                "SUCCESS"
            )
        elif response.status_code == 429:
            log(
                f"Request #{request_id} dibatasi server (HTTP 429)",
                "WARNING"
            )
        else:
            log(
                f"Request #{request_id} mendapat HTTP "
                f"{response.status_code} | {latency} ms",
                "WARNING"
            )

        response.close()
        return result

    except requests.RequestException as error:
        latency = round((time.perf_counter() - start) * 1000, 2)

        log(f"Request #{request_id} gagal: {error}", "ERROR")

        return {
            "id": request_id,
            "time": timestamp,
            "status": "ERROR",
            "latency_ms": latency,
            "error": str(error)
        }


# ==================================================
# ANALISIS HASIL
# ==================================================

def analyze(data):
    successful = [
        item for item in data
        if isinstance(item["status"], int)
        and 200 <= item["status"] < 400
    ]

    failed = len(data) - len(successful)

    latencies = [item["latency_ms"] for item in data]

    average = (
        round(sum(latencies) / len(latencies), 2)
        if latencies else 0
    )

    log("========== HASIL PENGUJIAN ==========")
    log(f"Total request : {len(data)}")
    log(f"Berhasil      : {len(successful)}")
    log(f"Gagal/lainnya : {failed}")
    log(f"Rata-rata     : {average} ms")
    log("=====================================")


# ==================================================
# SIMPAN LAPORAN CSV
# ==================================================

def save_report(data):
    import os

    os.makedirs(REPORT_DIR, exist_ok=True)

    filename = (
        f"{REPORT_DIR}/report_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )

    with open(filename, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "id", "time", "status", "latency_ms", "error"
            ]
        )

        writer.writeheader()
        writer.writerows(data)

    log(f"Laporan CSV disimpan: {filename}", "SUCCESS")


# ==================================================
# SIMPAN GRAFIK
# ==================================================

def save_graph(data):
    import os

    os.makedirs(REPORT_DIR, exist_ok=True)

    if not data:
        log("Tidak ada data untuk dibuat grafik.", "WARNING")
        return

    ids = [item["id"] for item in data]
    latencies = [item["latency_ms"] for item in data]

    plt.figure(figsize=(10, 5))
    plt.plot(ids, latencies, marker="o")
    plt.title("Grafik Latensi Pengujian HTTP")
    plt.xlabel("Nomor Request")
    plt.ylabel("Latensi (ms)")
    plt.grid(True)

    filename = (
        f"{REPORT_DIR}/graph_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    )

    plt.tight_layout()
    plt.savefig(filename)
    plt.close()

    log(f"Grafik disimpan: {filename}", "SUCCESS")


# ==================================================
# SIMULASI LOAD TEST TERBATAS
# ==================================================

def run_simulation(url, duration, rps):
    global results
    results = []

    log("Pengujian dimulai.", "INFO")
    log(f"Target   : {url}")
    log(f"Durasi   : {duration} detik")
    log(f"Kecepatan: {rps} request/detik")

    interval = 1 / rps
    start_time = time.monotonic()
    next_request = start_time
    request_id = 0
    pending = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:

        while time.monotonic() - start_time < duration:
            now = time.monotonic()

            if now < next_request:
                time.sleep(min(next_request - now, 0.05))
                continue

            request_id += 1
            log(f"Mengirim request #{request_id}")

            future = executor.submit(
                send_request, url, request_id
            )
            pending.append(future)

            next_request += interval

            # Ambil hasil yang sudah selesai tanpa menunggu semuanya.
            unfinished = []

            for task in pending:
                if task.done():
                    try:
                        results.append(task.result())
                    except Exception as error:
                        log(f"Kesalahan worker: {error}", "ERROR")
                else:
                    unfinished.append(task)

            pending = unfinished

        log("Waktu pengujian selesai. Menunggu request tersisa...")

        for future in as_completed(pending):
            try:
                results.append(future.result())
            except Exception as error:
                log(f"Kesalahan worker: {error}", "ERROR")

    results.sort(key=lambda item: item["id"])

    analyze(results)
    save_report(results)
    save_graph(results)

    log("Pengujian selesai.", "SUCCESS")


# ==================================================
# PROGRAM UTAMA
# ==================================================

def main():
    print("\n====================================")
    print("     DDOS By Mr.J4CK")
    print("====================================")
    print("Create By: JACKXCODE404")
    print("Rules: 5000 detik dan 10 request/detik.")
    print("====================================\n")

    url = input("Masukkan URL target: ").strip()

    try:
        url = validate_url(url)
    except ValueError as error:
        log(str(error), "ERROR")
        return

    try:
        duration = int(input("Durasi (1-5000 detik): "))
        rps = int(input("Request per detik (1-20): "))

        if not 1 <= duration <= MAX_DURATION:
            raise ValueError("Durasi harus 1 sampai 5000 detik.")

        if not 1 <= rps <= MAX_RPS:
            raise ValueError("RPS harus 1 sampai 20.")

    except ValueError as error:
        log(f"Input tidak valid: {error}", "ERROR")
        return

    print("\nTarget:", url)
    confirmation = input(
        "Ketik Y jika kamu berwenang menguji target ini: "
    ).strip()

    if confirmation != "Y":
        log("Pengujian dibatalkan.", "WARNING")
        return

    run_simulation(url, duration, rps)


if __name__ == "__main__":
    main()
