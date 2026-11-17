import csv
import json
import os
import re
import time
import socket
import ipaddress
import threading

from datetime import datetime
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import matplotlib.pyplot as plt


# =====================================================
# KONFIGURASI
# =====================================================

MAX_DURATION = 5000
MAX_RPS = 50
MAX_WORKERS = 50
REQUEST_TIMEOUT = 5

HOSTS_FILE = "authorized_hosts.json"
REPORT_DIR = "reports"

AUTHORIZED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1"
}

log_lock = threading.Lock()


# =====================================================
# SISTEM LOG
# =====================================================

def log(message, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with log_lock:
        print(
            f"[{timestamp}] [{level}] {message}",
            flush=True
        )


# =====================================================
# AUTHORIZED HOSTS
# =====================================================

def save_authorized_hosts():
    with open(HOSTS_FILE, "w", encoding="utf-8") as file:
        json.dump(
            sorted(AUTHORIZED_HOSTS),
            file,
            indent=4
        )


def load_authorized_hosts():
    global AUTHORIZED_HOSTS

    try:
        with open(HOSTS_FILE, "r", encoding="utf-8") as file:
            hosts = json.load(file)

        if not isinstance(hosts, list):
            raise ValueError("Format file host tidak valid.")

        for host in hosts:
            if isinstance(host, str) and is_valid_host(host):
                AUTHORIZED_HOSTS.add(host.lower())

    except FileNotFoundError:
        save_authorized_hosts()

    except (json.JSONDecodeError, OSError, ValueError) as error:
        log(f"Gagal membaca daftar host: {error}", "WARNING")


def is_valid_host(host):
    if not isinstance(host, str) or not host:
        return False

    host = host.strip().lower()

    if len(host) > 253:
        return False

    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass

    pattern = (
        r"(?=.{1,253}$)"
        r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)"
        r"(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*"
    )

    return bool(re.fullmatch(pattern, host))


def add_authorized_host():
    print("\n--- TAMBAH DOMAIN/IP ---")

    host = input("Masukkan domain/IP: ").strip().lower()

    if not is_valid_host(host):
        log("Format domain/IP tidak valid.", "ERROR")
        return

    if host in AUTHORIZED_HOSTS:
        log("Host sudah terdaftar.", "WARNING")
        return

    print("\nPastikan kamu pemilik target atau sudah")
    print("mendapatkan izin pengujian dari pemiliknya.")

    confirmation = input("Ketik y untuk menyimpan: ").strip()

    if confirmation != "y":
        log("Penambahan dibatalkan.", "WARNING")
        return

    AUTHORIZED_HOSTS.add(host)
    save_authorized_hosts()

    log(f"Host {host} berhasil ditambahkan.", "SUCCESS")


def view_authorized_hosts():
    print("\n--- DAFTAR DOMAIN/IP ---")

    for index, host in enumerate(
        sorted(AUTHORIZED_HOSTS), start=1
    ):
        print(f"{index}. {host}")

    print("-------------------------------")


def remove_authorized_host():
    view_authorized_hosts()

    host = input("\nMasukkan DOMAIN?IP yang ingin dihapus: ")
    host = host.strip().lower()

    if host in {"localhost", "127.0.0.1", "::1"}:
        log("Host lokal bawaan tidak dapat dihapus.", "WARNING")
        return

    if host not in AUTHORIZED_HOSTS:
        log("Host tidak ditemukan.", "WARNING")
        return

    confirmation = input("Ketik DELETE untuk menghapus: ")

    if confirmation != "DELETE":
        log("Penghapusan dibatalkan.", "WARNING")
        return

    AUTHORIZED_HOSTS.remove(host)
    save_authorized_hosts()

    log(f"Host {host} berhasil dihapus.", "SUCCESS")


def manage_authorized_hosts():
    while True:
        print("\n================================")
        print("      DOMAIN/IP")
        print("================================")
        print("1. Tambah DOMAIN/IP")
        print("2. Lihat daftar DOMAIN/IP")
        print("3. Hapus DOMAIN/IP")
        print("4. Kembali")
        print("================================")

        choice = input("Pilih menu: ").strip()

        if choice == "1":
            add_authorized_host()

        elif choice == "2":
            view_authorized_hosts()

        elif choice == "3":
            remove_authorized_host()

        elif choice == "4":
            break

        else:
            log("Pilihan tidak valid.", "WARNING")


# =====================================================
# VALIDASI TARGET
# =====================================================

def validate_url(url):
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError("Gunakan URL http:// atau https://")

    if not parsed.hostname:
        raise ValueError("Hostname tidak valid.")

    if parsed.username or parsed.password:
        raise ValueError("URL tidak boleh berisi kredensial.")

    hostname = parsed.hostname.lower()

    if hostname not in AUTHORIZED_HOSTS:
        raise ValueError(
            f"Host {hostname} belum terdaftar."
        )

    try:
        addresses = socket.getaddrinfo(
            hostname,
            parsed.port or (
                443 if parsed.scheme == "https" else 80
            )
        )
    except socket.gaierror:
        raise ValueError("Hostname tidak dapat ditemukan.")

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])

        if hostname not in {"localhost", "127.0.0.1", "::1"}:
            if (
                ip.is_loopback
                or ip.is_multicast
                or ip.is_unspecified
            ):
                raise ValueError(
                    "Alamat IP hasil DNS tidak diizinkan."
                )

    return url


# =====================================================
# HTTP REQUEST
# =====================================================

def send_request(url, request_id):
    start = time.perf_counter()

    timestamp = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
            headers={
                "User-Agent": "Authorized-Load-Test/1.0"
            }
        )

        latency = round(
            (time.perf_counter() - start) * 1000, 2
        )

        status = response.status_code
        response.close()

        if status == 200:
            log(
                f"Request #{request_id} berhasil | "
                f"HTTP {status} | {latency} ms",
                "SUCCESS"
            )

        elif status == 429:
            log(
                f"Request #{request_id} dibatasi server.",
                "WARNING"
            )

        else:
            log(
                f"Request #{request_id} | "
                f"HTTP {status} | {latency} ms",
                "WARNING"
            )

        return {
            "id": request_id,
            "time": timestamp,
            "status": status,
            "latency_ms": latency,
            "error": ""
        }

    except requests.RequestException as error:
        latency = round(
            (time.perf_counter() - start) * 1000, 2
        )

        log(
            f"Request #{request_id} gagal: {error}",
            "ERROR"
        )

        return {
            "id": request_id,
            "time": timestamp,
            "status": "ERROR",
            "latency_ms": latency,
            "error": str(error)
        }


# =====================================================
# ANALISIS
# =====================================================

def analyze(data):
    successful = [
        item for item in data
        if isinstance(item["status"], int)
        and 200 <= item["status"] < 400
    ]

    failed = len(data) - len(successful)

    latencies = [
        item["latency_ms"] for item in data
    ]

    average = (
        round(sum(latencies) / len(latencies), 2)
        if latencies else 0
    )

    print("\n========== HASIL PENGUJIAN ==========")
    print(f"Total request : {len(data)}")
    print(f"Berhasil      : {len(successful)}")
    print(f"Gagal/lainnya : {failed}")
    print(f"Rata-rata     : {average} ms")
    print("=====================================")


# =====================================================
# SIMPAN LAPORAN CSV
# =====================================================

def save_report(data):
    os.makedirs(REPORT_DIR, exist_ok=True)

    filename = (
        f"{REPORT_DIR}/report_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )

    with open(
        filename, "w", newline="", encoding="utf-8"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=[
                "id",
                "time",
                "status",
                "latency_ms",
                "error"
            ]
        )

        writer.writeheader()
        writer.writerows(data)

    log(f"Laporan disimpan: {filename}", "SUCCESS")


# =====================================================
# GRAFIK LATENSI
# =====================================================

def save_graph(data):
    if not data:
        log("Tidak ada data grafik.", "WARNING")
        return

    os.makedirs(REPORT_DIR, exist_ok=True)

    ids = [item["id"] for item in data]
    latencies = [
        item["latency_ms"] for item in data
    ]

    plt.figure(figsize=(10, 5))
    plt.plot(ids, latencies, marker="o")

    plt.title("HTTP Load Test - Latency")
    plt.xlabel("Request")
    plt.ylabel("Latency (ms)")
    plt.grid(True)

    filename = (
        f"{REPORT_DIR}/graph_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    )

    plt.tight_layout()
    plt.savefig(filename)
    plt.close()

    log(f"Grafik disimpan: {filename}", "SUCCESS")


# =====================================================
# LOAD TEST TERBATAS
# =====================================================

def run_simulation(url, duration, rps):
    results = []

    log("Pengujian dimulai.")
    log(f"Target   : {url}")
    log(f"Durasi   : {duration} detik")
    log(f"Kecepatan: {rps} request/detik")

    interval = 1 / rps
    start_time = time.monotonic()
    next_request = start_time
    request_id = 0
    pending = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        while time.monotonic() - start_time < duration:

            now = time.monotonic()

            if now < next_request:
                time.sleep(
                    min(next_request - now, 0.05)
                )
                continue

            request_id += 1

            log(f"Mengirim request #{request_id}")

            future = executor.submit(
                send_request,
                url,
                request_id
            )

            pending.append(future)
            next_request += interval

            unfinished = []

            for task in pending:
                if task.done():
                    try:
                        results.append(task.result())
                    except Exception as error:
                        log(
                            f"Worker error: {error}",
                            "ERROR"
                        )
                else:
                    unfinished.append(task)

            pending = unfinished

        log("Waktu pengujian berakhir.")

        for future in as_completed(pending):
            try:
                results.append(future.result())
            except Exception as error:
                log(
                    f"Worker error: {error}",
                    "ERROR"
                )

    results.sort(key=lambda item: item["id"])

    analyze(results)
    save_report(results)
    save_graph(results)

    log("Pengujian selesai.", "SUCCESS")


# =====================================================
# MULAI PENGUJIAN
# =====================================================

def start_test():
    print("\n--- MULAI PENGUJIAN ---")

    url = input("Masukkan URL target: ").strip()

    try:
        url = validate_url(url)
    except ValueError as error:
        log(str(error), "ERROR")
        return

    try:
        duration = int(
            input("Durasi (1-5000 detik): ")
        )

        rps = int(
            input("Request per detik (1-50): ")
        )

        if not 1 <= duration <= MAX_DURATION:
            raise ValueError(
                "Durasi harus 1 sampai 5000 detik."
            )

        if not 1 <= rps <= MAX_RPS:
            raise ValueError(
                "RPS harus 1 sampai 50."
            )

    except ValueError as error:
        log(str(error), "ERROR")
        return

    print("\nTarget:", url)
    print("Pastikan target milikmu atau sudah memberi izin.")

    confirmation = input(
        "Ketik y untuk melanjutkan: "
    ).strip()

    if confirmation != "y":
        log("Pengujian dibatalkan.", "WARNING")
        return

    run_simulation(url, duration, rps)


# =====================================================
# MENU UTAMA
# =====================================================

def main():
    load_authorized_hosts()

    while True:
        print("\n====================================")
        print("      DDOS By Mr.J4CK")
        print("   Create By: JACKXCODE404")
        print("====================================")
        print("1. Mulai pengujian")
        print("2. Kelola DOMAIN/IP")
        print("3. Keluar")
        print("====================================")

        choice = input("Pilih menu: ").strip()

        if choice == "1":
            start_test()

        elif choice == "2":
            manage_authorized_hosts()

        elif choice == "3":
            log("Program ditutup.")
            break

        else:
            log("Pilihan tidak valid.", "WARNING")


# =====================================================
# PROGRAM UTAMA
# =====================================================

if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print("\n")
        log("Program dihentikan pengguna.", "WARNING")
