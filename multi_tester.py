import asyncio
import base64
import json
import math
import os
import re
import subprocess
import tempfile
import time
from statistics import mean
from urllib.parse import parse_qs, unquote, urlparse
import httpx
from settings import *

# Глобальный список для отслеживания PID дочерних процессов
RUNNING_PIDS = set()
TEST_ERROR_COUNTS = {}

# ============================================================
# GIT
# ============================================================

def run_git_command(args):
    try:
        result = subprocess.run(
            args,
            cwd=REPO_PATH,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        output = result.stdout.strip()
        if output:
            print(output)
        return True
    except subprocess.TimeoutExpired:
        print(f"Ошибка Git: команда зависла: {' '.join(args)}")
        return False
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        print(f"Ошибка Git при выполнении {' '.join(args)}: {stderr}")
        return False
    except Exception as e:
        print(f"Ошибка Git: {e}")
        return False

def push_to_git():
    print("[i] Публикация GitHub выполняется главным окном PULSEWAVE после проверки.")

# ============================================================
# PROCESS CLEANUP
# ============================================================

def kill_process_tree(proc=None):
    if proc is not None:
        try:
            RUNNING_PIDS.discard(proc.pid)
            if proc.poll() is None:
                print(f"[i] Завершение Sing-box PID={proc.pid}...")
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/f", "/t", "/pid", str(proc.pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
        except Exception as e:
            print(f"[!] Ошибка завершения PID: {e}")
        try:
            proc.wait(timeout=5)
        except Exception:
            pass

def kill_old_vpn_processes():
    print("[*] Безопасная очистка запущенных скриптом VPN-процессов...")
    if not RUNNING_PIDS:
        return
    
    pids_to_kill = list(RUNNING_PIDS)
    for pid in pids_to_kill:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
            RUNNING_PIDS.discard(pid)
        except Exception:
            pass

async def cleanup_after_batch(proc):
    kill_process_tree(proc)
    await asyncio.sleep(CLEANUP_DELAY)

# ============================================================
# CHECKPOINT
# ============================================================

def atomic_write_json(filename, data):
    temp_filename = filename + ".tmp"
    try:
        with open(temp_filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
        os.replace(temp_filename, filename)
        return True
    except Exception as e:
        print(f"[-] Ошибка сохранения checkpoint: {e}")
        try:
            if os.path.exists(temp_filename):
                os.remove(temp_filename)
        except Exception:
            pass
        return False

def load_checkpoint():
    if not os.path.exists(CHECKPOINT_FILENAME):
        return None
    try:
        with open(CHECKPOINT_FILENAME, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        print("[+] Найден checkpoint.")
        return data
    except Exception as e:
        print(f"[!] Не удалось загрузить checkpoint: {e}")
        return None

def save_checkpoint(batch_index, total_batches, working_configs, processed_keys):
    data = {
        "version": 2,
        "batch_index": batch_index,
        "total_batches": total_batches,
        "working_configs": working_configs,
        "processed_keys": list(processed_keys),
        "saved_at": time.time(),
    }
    return atomic_write_json(CHECKPOINT_FILENAME, data)

def delete_checkpoint():
    try:
        if os.path.exists(CHECKPOINT_FILENAME):
            os.remove(CHECKPOINT_FILENAME)
            print(f"[+] Checkpoint {CHECKPOINT_FILENAME} удален.")
    except OSError as e:
        print(f"[!] Не удалось удалить checkpoint: {e}")

# ============================================================
# DOWNLOAD CONFIGS
# ============================================================

def _extract_proxy_links(text):
    """Extract plain links and decode common Base64 subscription payloads."""
    pattern = r'((?:vless|trojan|vmess)://[^\s]+)'
    found = re.findall(pattern, text or "", flags=re.IGNORECASE)

    # Many subscription providers return a Base64-encoded list instead of
    # readable text. Decode only when the result actually contains proxy URIs.
    if not found:
        compact = re.sub(r"\\s+", "", text or "")
        if compact and len(compact) >= 24:
            try:
                padded = compact + "=" * (-len(compact) % 4)
                decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode(
                    "utf-8", errors="ignore"
                )
                if re.search(r"(?:vless|trojan|vmess)://", decoded, re.IGNORECASE):
                    found = re.findall(
                        pattern, decoded, flags=re.IGNORECASE
                    )
            except Exception:
                pass

    valid_links = set()
    for link in found:
        link = link.strip().strip("\\'\\\"").rstrip(".,;)(\\\'\\\"}")
        if link.lower().startswith(("vless://", "trojan://", "vmess://")):
            valid_links.add(link)
    return list(valid_links)


def fetch_one_source(url):
    try:
        response = httpx.get(url, timeout=20.0, follow_redirects=True)
        if response.status_code != 200:
            return url, [], f"HTTP {response.status_code}"
        valid_links = _extract_proxy_links(response.text)
        return url, valid_links, None
    except Exception as e:
        return url, [], str(e)

async def fetch_and_filter_links_async(sources):
    if not sources:
        print("[-] Список источников пуст. Проверьте sources.txt рядом с приложением.")
        return []
    print(f"[*] Параллельное скачивание конфигураций из {len(sources)} источников...")
    semaphore = asyncio.Semaphore(8)
    async def worker(url):
        async with semaphore:
            return await asyncio.to_thread(fetch_one_source, url)
    results = await asyncio.gather(*[worker(url) for url in sources], return_exceptions=True)
    valid_links = set()
    for result in results:
        if isinstance(result, Exception):
            print(f"  - Ошибка источника: {result}")
            continue
        url, links, error = result
        if error:
            print(f"  - Ошибка: {url.split('/')[-1]}: {error}")
            continue
        print(f"  - Найдено ссылок {url.split('/')[-1]}: {len(links)}")
        valid_links.update(links)
    print(f"[+] Всего уникальных ссылок из источников: {len(valid_links)}")
    return list(valid_links)

# ============================================================
# PARSER & VALIDATION
# ============================================================

def parse_proxy_link(link):
    try:
        link_lower = link.lower()
        if link_lower.startswith("vmess://"):
            b64_content = link[8:].strip()
            b64_content += "=" * (-len(b64_content) % 4)
            json_str = base64.b64decode(b64_content).decode("utf-8", errors="ignore")
            c = json.loads(json_str)
            return {
                "protocol": "vmess",
                "name": c.get("ps", "Без имени"),
                "address": c.get("add"),
                "port": int(c.get("port", 443)),
                "uuid": c.get("id"),
                "security": c.get("tls") or "none",
                "network": c.get("net", "tcp"),
                "path": c.get("path", ""),
                "sni": c.get("sni") or c.get("host") or "",
                "host": c.get("host", ""),
            }
        parsed = urlparse(link)
        protocol = parsed.scheme.lower()
        name = unquote(parsed.fragment) if parsed.fragment else "Без имени"
        query_params = parse_qs(parsed.query, keep_blank_values=True)
        def get_param(key):
            res = query_params.get(key, [None])
            return res[0] if res else None
        data = {
            "protocol": protocol,
            "name": name,
            "address": parsed.hostname,
            "port": int(parsed.port) if parsed.port else 443,
        }
        if protocol in ["vless", "trojan"]:
            data.update({
                "uuid": parsed.username,
                "security": get_param("security") or "none",
                "network": get_param("type") or "tcp",
                "sni": get_param("sni") or "",
                "fp": get_param("fp") or "chrome",
                "pbk": get_param("pbk") or "",
                "sid": get_param("sid") or "",
                "flow": get_param("flow") or "",
                "path": get_param("path") or "",
                "host": get_param("host") or "",
            })
        return data
    except Exception:
        return None

def is_supported_server(data):
    if not data:
        return False
    protocol = data.get("protocol")
    if protocol not in ["vless", "vmess", "trojan"]:
        return False
    if not data.get("address") or not data.get("port"):
        return False
    if protocol in ["vless", "trojan"]:
        if not data.get("uuid"):
            return False
        if data.get("security") == "reality":
            pub_key = data.get("pbk", "").strip()
            if len(pub_key) != 43:
                return False
    if protocol == "vmess" and not data.get("uuid"):
        return False
    return True

def server_fingerprint(data):
    fields = ["protocol", "address", "port", "uuid", "security", "network", "sni", "fp", "pbk", "sid", "flow", "path", "host"]
    values = []
    for field in fields:
        value = data.get(field, "")
        values.append(str(value).strip().lower())
    return "|".join(values)

# ============================================================
# SING-BOX OUTBOUND & CONFIGURATION
# ============================================================

def build_outbound(data, tag):
    protocol = data["protocol"]
    outbound = {
        "type": protocol,
        "tag": tag,
        "server": data["address"],
        "server_port": data["port"],
    }
    allowed_fingerprints = ["chrome", "firefox", "safari", "edge", "android", "ios"]
    if protocol == "vless":
        outbound["uuid"] = data["uuid"]
        if "vision" in data.get("flow", ""):
            outbound["flow"] = "xtls-rprx-vision"
        if data.get("security") in ["tls", "reality"]:
            tls_block = {"enabled": True}
            if data.get("sni"):
                tls_block["server_name"] = data["sni"]
            fp_value = (data.get("fp") or "chrome").lower()
            if fp_value not in allowed_fingerprints:
                fp_value = "chrome"
            tls_block["utls"] = {"enabled": True, "fingerprint": fp_value}
            tls_block["fragment"] = True
            if data.get("security") == "reality":
                tls_block["reality"] = {
                    "enabled": True,
                    "public_key": data.get("pbk", "").strip(),
                    "short_id": data.get("sid", "").strip(),
                }
            outbound["tls"] = tls_block
    elif protocol == "trojan":
        outbound["password"] = data["uuid"]
        if data.get("security") in ["tls", "reality"]:
            tls_block = {"enabled": True}
            if data.get("sni"):
                tls_block["server_name"] = data["sni"]
            fp_value = (data.get("fp") or "chrome").lower()
            if fp_value not in allowed_fingerprints:
                fp_value = "chrome"
            tls_block["utls"] = {"enabled": True, "fingerprint": fp_value}
            tls_block["fragment"] = True
            if data.get("security") == "reality":
                tls_block["reality"] = {
                    "enabled": True,
                    "public_key": data.get("pbk", "").strip(),
                    "short_id": data.get("sid", "").strip(),
                }
            outbound["tls"] = tls_block
    elif protocol == "vmess":
        outbound.update({"uuid": data["uuid"], "security": "auto"})
        if data.get("security") == "tls":
            tls_block = {"enabled": True}
            if data.get("sni"):
                tls_block["server_name"] = data["sni"]
            tls_block["fragment"] = True
            outbound["tls"] = tls_block
        if data.get("network") == "ws":
            host_header = data.get("host") or data.get("sni") or data["address"]
            outbound["transport"] = {
                "type": "ws",
                "path": data.get("path", ""),
                "headers": {"Host": host_header},
            }
    return outbound

def generate_singbox_config(servers_list):
    inbounds = []
    outbounds = []
    rules = []
    valid_servers = []
    for link, data in servers_list:
        if not is_supported_server(data):
            continue
        valid_servers.append((link, data))
    for index, (link, data) in enumerate(valid_servers):
        tag = f"proxy_{index}"
        inbound_tag = f"in_{tag}"
        local_port = LOCAL_PORT_START + index
        inbounds.append({
            "type": "socks",
            "tag": inbound_tag,
            "listen": "127.0.0.1",
            "listen_port": local_port,
        })
        outbounds.append(build_outbound(data, tag))
        rules.append({"inbound": [inbound_tag], "outbound": tag})
    config = {
        "log": {"level": "error"},
        "dns": {
            "servers": [{"type": "udp", "tag": "dns_direct", "server": "1.1.1.1"}]
        },
        "inbounds": inbounds,
        "outbounds": outbounds,
        "route": {"rules": rules},
    }
    with open(SINGBOX_CONFIG, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    return valid_servers

def start_singbox():
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(
            [os.path.abspath(SINGBOX_PATH), "run", "-c", os.path.abspath(SINGBOX_CONFIG)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        if proc and proc.pid:
            RUNNING_PIDS.add(proc.pid)
        return proc
    except Exception as e:
        print(f"[-] Не удалось запустить Sing-box: {e}")
        return None

def check_singbox_config():
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        result = subprocess.run(
            [os.path.abspath(SINGBOX_PATH), "check", "-c", os.path.abspath(SINGBOX_CONFIG)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=creationflags,
        )
        if result.returncode != 0:
            print("[-] Sing-box отклонил конфигурацию:")
            output = (result.stderr or result.stdout or "").strip()
            if output:
                print(output)
            return False
        return True
    except Exception as e:
        print(f"[-] Ошибка проверки конфига Sing-box: {e}")
        return False

# ============================================================
# TESTING, BENCHMARKING AND CORE EXECUTION LOOP
# ============================================================

async def test_one_proxy(local_port, link, name):
    proxy_url = f"socks5://127.0.0.1:{local_port}"
    timeout = httpx.Timeout(
        connect=HTTP_CONNECT_TIMEOUT,
        read=HTTP_READ_TIMEOUT,
        write=HTTP_WRITE_TIMEOUT,
        pool=HTTP_POOL_TIMEOUT,
    )
    start_time = time.monotonic()
    try:
        limits = httpx.Limits(max_connections=1, max_keepalive_connections=0)
        async with httpx.AsyncClient(
            proxy=proxy_url,
            timeout=timeout,
            verify=True,
            follow_redirects=True,
            limits=limits,
        ) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5"
            }
            response = await client.get(TEST_URLS, headers=headers)
            if response.status_code not in (200, 301, 302) or not response.text or len(response.text) < 200:
                return None
            ip_response = await client.get(IP_CHECK_URL, headers=headers)
            if ip_response.status_code != 200:
                return None
            try:
                ip_data = ip_response.json()
                external_ip = ip_data.get("ip")
                if not external_ip or not re.match(r"^[0-9a-fA-F.:]+$", external_ip):
                    return None
            except Exception:
                return None
            elapsed = round((time.monotonic() - start_time) * 1000)
            parsed = parse_proxy_link(link) or {}
            return {
                "ping": elapsed,
                "link": link,
                "name": name,
                "external_ip": external_ip,
                "protocol": parsed.get("protocol", "unknown"),
                "success": True,
            }
    except asyncio.CancelledError:
        raise
    except Exception as e:
        # Keep failures visible: previously every transport error was silently
        # converted to None, making missing SOCKS support look like dead servers.
        error_key = f"{type(e).__name__}: {str(e)[:180]}"
        TEST_ERROR_COUNTS[error_key] = TEST_ERROR_COUNTS.get(error_key, 0) + 1
        return None

def progress_bar(completed, total, width=30):
    if total <= 0:
        return ""
    ratio = min(completed / total, 1.0)
    filled = int(width * ratio)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {completed}/{total}"

def print_progress(completed, total, results, started, active):
    elapsed = time.monotonic() - started
    if completed:
        avg_time = elapsed / completed
        remaining = max(total - completed, 0)
        eta = remaining * avg_time
    else:
        eta = 0
    if results:
        avg_ping = round(mean(r["ping"] for r in results))
        best_ping = min(r["ping"] for r in results)
    else:
        avg_ping = 0
        best_ping = 0
    line = (
        f"\r{progress_bar(completed, total)} "
        f"| OK: {len(results):>3} | active: {active:>3} "
        f"| avg: {avg_ping:>4} ms | best: {best_ping:>4} ms | ETA: {eta:>4.1f}s"
    )
    print(line, end="", flush=True)

def adjust_concurrency(completed, failures, elapsed):
    global current_concurrency
    if completed <= 0:
        return
    failure_rate = failures / completed
    if failure_rate >= 0.75:
        current_concurrency = max(MIN_CONCURRENT_TESTS, current_concurrency - 20)
        return
    if failure_rate <= 0.25 and elapsed < BATCH_TIMEOUT * 0.75:
        current_concurrency = min(MAX_CONCURRENT_TESTS, current_concurrency + 10)

async def test_batch(valid_servers, batch_number, total_batches):
    global current_concurrency
    total = len(valid_servers)
    if total == 0:
        return []
    semaphore = asyncio.Semaphore(current_concurrency)
    results = []
    completed = 0
    failures = 0
    TEST_ERROR_COUNTS.clear()
    started = time.monotonic()
    print()
    print(f"[i] Пачка {batch_number}/{total_batches}: {total} конфигов | concurrency={current_concurrency}")
    async def worker(index, link, data):
        nonlocal completed, failures
        async with semaphore:
            local_port = LOCAL_PORT_START + index
            try:
                res = await test_one_proxy(local_port, link, data.get("name", "Без имени"))
                completed += 1
                if res:
                    results.append(res)
                    print("@@VLESS_RESULT@@" + json.dumps(res, ensure_ascii=False), flush=True)
                else:
                    failures += 1
                print("@@VLESS_PROGRESS@@" + json.dumps({"completed": completed, "total": total, "working": len(results), "active": max(total-completed, 0), "concurrency": current_concurrency}), flush=True)
                active = total - completed
                print_progress(completed, total, results, started, active)
                return res
            except Exception as e:
                print(f"\n[!] Сбой воркера на порту {local_port}: {e}")
                completed += 1
                failures += 1
                print("@@VLESS_PROGRESS@@" + json.dumps({"completed": completed, "total": total, "working": len(results), "active": max(total-completed, 0), "concurrency": current_concurrency}), flush=True)
                return None
    tasks = [worker(index, link, data) for index, (link, data) in enumerate(valid_servers)]
    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=BATCH_TIMEOUT)
    except asyncio.TimeoutError:
        print()
        print(f"[!] Пачка {batch_number}: достигнут общий таймаут пачки {BATCH_TIMEOUT}s.")
    except Exception as e:
        print(f"\n[-] Системная ошибка планировщика: {e}")
    elapsed = time.monotonic() - started
    adjust_concurrency(completed, failures, elapsed)
    if TEST_ERROR_COUNTS:
        print("[!] Причины ошибок проверки (сгруппировано):")
        for error, count in sorted(TEST_ERROR_COUNTS.items(), key=lambda item: -item[1])[:8]:
            print(f"    {count}x {error}")
    print()
    print(f"[+] Пачка {batch_number}: {len(results)} рабочих | {completed}/{total} проверено | {elapsed:.1f}s | следующая concurrency={current_concurrency}")
    return results

def load_old_configs():
    if not os.path.exists(OUTPUT_FILENAME):
        return []
    print(f"[*] Загружаем старые конфигурации из {OUTPUT_FILENAME}...")
    configs = []
    try:
        with open(OUTPUT_FILENAME, "r", encoding="utf-8") as f:
            for line in f:
                link = line.strip()
                if not link:
                    continue
                data = parse_proxy_link(link)
                if not is_supported_server(data):
                    continue
                configs.append((link, data))
        print(f"[+] Старых конфигураций: {len(configs)}")
        return configs
    except Exception as e:
        print(f"[!] Ошибка чтения {OUTPUT_FILENAME}: {e}")
        return []

def modify_link_for_output(link):
    try:
        if "#" in link:
            main_part, name_part = link.split("#", 1)
        else:
            main_part = link
            name_part = ""
        query_lower = main_part.lower()
        if "?" in main_part:
            if not re.search(r"(?:[?&])fp=", query_lower):
                main_part += "&fp=chrome"
        else:
            main_part += "?fp=chrome"
        return f"{main_part}#{name_part}" if name_part else main_part
    except Exception:
        return link

def calculate_score(result, max_ping):
    ping = result["ping"]
    if max_ping <= 0:
        speed_score = 100
    else:
        speed_score = 100 * (1 - (ping / max_ping))
    speed_score = max(0, speed_score)
    tunnel_bonus = 15 if result.get("protocol") == "tunnel" else 0
    return speed_score * 0.85 + tunnel_bonus

def rank_results(results):
    if not results:
        return []
    max_ping = max(r["ping"] for r in results)
    ranked = []
    for result in results:
        if "score" in result:
            ranked.append(dict(result))
            continue
        score = calculate_score(result, max_ping)
        item = dict(result)
        item["score"] = round(score, 2)
        ranked.append(item)
    ranked.sort(key=lambda x: (-x["score"], x["ping"]))
    return ranked

def result_to_checkpoint(result):
    return {
        "ping": result["ping"],
        "link": result["link"],
        "name": result.get("name", "Без имени"),
        "external_ip": result.get("external_ip", ""),
        "score": result.get("score", 0),
    }

def checkpoint_to_result(item):
    try:
        return {
            "ping": int(item["ping"]),
            "link": item["link"],
            "name": item.get("name", "Без имени"),
            "external_ip": item.get("external_ip", ""),
            "success": True,
            "score": float(item.get("score", 0)),
        }
    except Exception:
        return None

def save_results(working_configs, already_ranked=False):
    if not working_configs:
        print("[-] Найдено 0 рабочих конфигураций. Перезапись файла отменена, старые данные сохранены.")
        return False
    ranked = working_configs if already_ranked else rank_results(working_configs)
    top_configs = ranked[:TARGET_WORKING]
    try:
        temp_file = OUTPUT_FILENAME + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            for result in top_configs:
                link = modify_link_for_output(result["link"])
                f.write(link + "\n")
            f.flush()
        os.replace(temp_file, OUTPUT_FILENAME)
        print(f"[+] ТОП-{len(top_configs)} сохранен в {OUTPUT_FILENAME}")
        return True
    except Exception as e:
        print(f"[-] Ошибка записи результата: {e}")
        return False

def parse_links(links):
    parsed = []
    seen = set()
    for link in links:
        data = parse_proxy_link(link)
        if not is_supported_server(data):
            continue
        key = server_fingerprint(data)
        if key in seen:
            continue
        seen.add(key)
        parsed.append((link, data))
    return parsed

async def run_test_group(servers, label):
    if not servers:
        return []
    print()
    print("=" * 75)
    print(f"[+] {label}: {len(servers)} конфигураций")
    kill_old_vpn_processes()
    await asyncio.sleep(0.3)
    try:
        valid_servers = generate_singbox_config(servers)
    except Exception as e:
        print(f"[-] Ошибка генерации конфига: {e}")
        return []
    if not valid_servers:
        print("[-] Нет подходящих конфигураций.")
        return []
    if not check_singbox_config():
        print("[-] Конфиг Sing-box не прошёл проверку.")
        return []
    proc = start_singbox()
    if proc is None:
        return []
    print(f"[i] Sing-box запущен. PID={proc.pid}")
    await asyncio.sleep(SINGBOX_START_DELAY)
    if proc.poll() is not None:
        print("[-] Sing-box завершился сразу после запуска.")
        kill_process_tree(proc)
        return []
    try:
        results = await test_batch(valid_servers, 1, 1)
    except Exception as e:
        print(f"[-] Ошибка тестирование: {e}")
        results = []
    await cleanup_after_batch(proc)
    return results

# ============================================================
# MAIN EXECUTION LOOP
# ============================================================

async def main_async():
    global current_concurrency
    kill_old_vpn_processes()
    if not os.path.exists(SINGBOX_PATH):
        print(f"[-] Ошибка: ядро {SINGBOX_PATH} не найдено.")
        return
    checkpoint = load_checkpoint()
    working_configs = []
    if checkpoint:
        for item in checkpoint.get("working_configs", []):
            result = checkpoint_to_result(item)
            if result:
                working_configs.append(result)
        print(f"[+] Восстановлено {len(working_configs)} результатов из checkpoint.")
    old_configs = load_old_configs()
    if old_configs:
        print()
        print("[*] Повторно проверяем старые рабочие конфиги...")
        old_results = await run_test_group(old_configs, "ПОВТОРНАЯ ПРОВЕРКА СТАРЫХ")
        if old_results:
            working_configs.extend(old_results)
            unique_results = {}
            for result in working_configs:
                data = parse_proxy_link(result["link"])
                if not data:
                    continue
                key = server_fingerprint(data)
                current = unique_results.get(key)
                if current is None or result["ping"] < current["ping"]:
                    unique_results[key] = result
            working_configs = list(unique_results.values())
            print(f"[+] После повторной проверки осталось {len(working_configs)} рабочих старых конфигов.")
            save_results(working_configs)
    if TARGET_WORKING > 0 and len(working_configs) >= TARGET_WORKING:
        print("\n[+] Старых рабочих конфигураций уже достаточно.")
        save_results(working_configs)
        push_to_git()
        delete_checkpoint()
        return
    links = await fetch_and_filter_links_async(SOURCES)
    if not links:
        print("[-] Новых ссылок для тестов нет.")
        if working_configs:
            save_results(working_configs)
            push_to_git()
        return
    parsed_servers = parse_links(links)
    print()
    print(f"[*] Уникальных новых конфигураций: {len(parsed_servers)}")
    if not parsed_servers:
        print("[-] После парсинга новых конфигураций не осталось.")
        if working_configs:
            save_results(working_configs)
            push_to_git()
        return
    existing_keys = set()
    for result in working_configs:
        data = parse_proxy_link(result["link"])
        if data:
            existing_keys.add(server_fingerprint(data))
    new_servers = []
    for link, data in parsed_servers:
        key = server_fingerprint(data)
        if key in existing_keys:
            continue
        new_servers.append((link, data))
    print(f"[*] После исключения уже рабочих: {len(new_servers)} новых.")
    total_proxies = len(new_servers)
    if total_proxies == 0:
        print("[i] Новых конфигураций для проверки нет.")
        save_results(working_configs)
        push_to_git()
        delete_checkpoint()
        return
    total_batches = (total_proxies + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"[*] Пачек: {total_batches} | Размер: {BATCH_SIZE} | Начальная concurrency: {current_concurrency} | Цель: {TARGET_WORKING}")
    try:
        for batch_index in range(total_batches):
            if TARGET_WORKING > 0 and len(working_configs) >= TARGET_WORKING:
                print("\n[+] Достигнута цель по рабочим.")
                break
            start_index = batch_index * BATCH_SIZE
            end_index = min(start_index + BATCH_SIZE, total_proxies)
            raw_batch = new_servers[start_index:end_index]
            batch_number = batch_index + 1
            print()
            print("=" * 75)
            print(f"[+] Новая пачка {batch_number}/{total_batches} | {start_index + 1}-{end_index} из {total_proxies}")
            results = await run_test_group(raw_batch, f"НОВАЯ ПАЧКА {batch_number}/{total_batches}")
            if results:
                working_configs.extend(results)
            if working_configs:
                unique_results = {}
                for result in working_configs:
                    data = parse_proxy_link(result["link"])
                    if not data:
                        continue
                    key = server_fingerprint(data)
                    old = unique_results.get(key)
                    if old is None or result["ping"] < old["ping"]:
                        unique_results[key] = result
                working_configs = list(unique_results.values())
                ranked = rank_results(working_configs)
                save_results(ranked, already_ranked=True)
                save_checkpoint(batch_index + 1, total_batches, [result_to_checkpoint(r) for r in ranked], set())
            print(f"[i] Рабочих всего: {len(working_configs)}/{TARGET_WORKING}")
    except KeyboardInterrupt:
        print("\n[!] Получен Ctrl+C.")
    except Exception as e:
        print(f"\n[-] Критическая ошибка: {e}")
    finally:
        print("\n[*] Финальная очистка VPN-процессов...")
        kill_old_vpn_processes()
    print()
    print("-" * 75)
    print("[*] Полное тестирование завершено!")
    print(f"[*] Рабочих найдено: {len(working_configs)}")
    save_results(working_configs)
    push_to_git()
    delete_checkpoint()
    if os.path.exists(SINGBOX_CONFIG):
        try:
            os.remove(SINGBOX_CONFIG)
            print(f"[+] Временный файл {SINGBOX_CONFIG} удален.")
        except OSError as e:
            print(f"[-] Не удалось удалить {SINGBOX_CONFIG}: {e}")
    print("\n[+] Работа завершена.")

# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n[!] Программа остановлена пользователем.")
        kill_old_vpn_processes()
        if os.path.exists(SINGBOX_CONFIG):
            try:
                os.remove(SINGBOX_CONFIG)
            except OSError:
                pass
    except Exception as e:
        print(f"\n[-] Необработанная ошибка: {e}")
        kill_old_vpn_processes()
        if os.path.exists(SINGBOX_CONFIG):
            try:
                os.remove(SINGBOX_CONFIG)
            except OSError:
                pass
