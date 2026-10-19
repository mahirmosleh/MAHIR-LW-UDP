# -*- coding: utf-8 -*-
"""
FreeFire Level Up Bot - Professional Web Dashboard & Real-Time EXP Tracker
Embedded Async Web Server (aiohttp)

MAHIR Edition:
- Auto Lone Wolf mode from Level 3 onwards
- Per-account EXP limit
- Account detail (password, exp_limit, mode, etc.)
- Pause / Resume per-account support
"""

import asyncio
import json
import os
import time
from typing import Dict, List, Any, Optional
from aiohttp import web

# ==================== CONFIG ====================
# 🔥 Auto Lone Wolf kicks in at this level (or above)
AUTO_LONE_WOLF_LEVEL = 3


# ==================== BOT STATE ====================
class BotState:
    def __init__(self):
        self.accounts: Dict[str, Dict[str, Any]] = {}
        self.logs: List[Dict[str, Any]] = []
        self.max_logs = 200
        self.total_matches = 0
        self.total_gained_exp = 0
        self.start_time = time.time()
        self.account_workers: Dict[str, asyncio.Task] = {}
        self.refresh_callbacks: Dict[str, Any] = {}
        self.account_credentials: Dict[str, Dict[str, Any]] = {}

    # ---------- logging ----------
    def log(self, message: str, level: str = "info", uid: Optional[str] = None):
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "level": level,
            "message": message,
            "uid": uid,
        }
        self.logs.append(entry)
        if len(self.logs) > self.max_logs:
            self.logs.pop(0)

    # ---------- account registration ----------
    def register_account(self, uid: str, nickname: str, region: str,
                         level: int, exp: int, likes: int = 0):
        uid_str = str(uid)
        lvl_val = max(1, int(level or 1))

        if uid_str not in self.accounts:
            self.accounts[uid_str] = {
                "uid": uid_str,
                "nickname": nickname or f"Player_{uid_str[:6]}",
                "region": region or "BD",
                "level": lvl_val,
                "initial_exp": exp,
                "current_exp": exp,
                "gained_exp": 0,
                "likes": likes or 0,
                "exp_limit": 0,
                "mode": "BR",
                "mode_manual": False,
                "paused": False,
                "status": "ONLINE",
                "matches_played": 0,
                "active_matches": 0,
                "last_match_time": None,
                "last_updated": time.strftime("%H:%M:%S"),
            }
        else:
            acc = self.accounts[uid_str]
            if nickname:
                acc["nickname"] = nickname
            if region:
                acc["region"] = region
            if level:
                acc["level"] = lvl_val
            acc["current_exp"] = exp
            acc["gained_exp"] = max(0, exp - acc["initial_exp"])
            acc["likes"] = likes
            if not acc.get("mode_manual", False):
                acc["mode"] = "BR"
            acc.setdefault("paused", False)
            acc["status"] = "PAUSED" if acc.get("paused", False) else "ONLINE"
            acc["last_updated"] = time.strftime("%H:%M:%S")

        self.recalc_totals()

    # ---------- EXP update ----------
    def update_exp(self, uid: str, current_exp: int, level: Optional[int] = None):
        uid_str = str(uid)
        if uid_str not in self.accounts:
            return
        acc = self.accounts[uid_str]
        old_exp = acc["current_exp"]
        acc["current_exp"] = current_exp
        if level is not None and level > 0:
            acc["level"] = int(level)
        if not acc.get("mode_manual", False):
            acc["mode"] = "BR"
        acc["gained_exp"] = max(0, current_exp - acc["initial_exp"])
        acc["last_updated"] = time.strftime("%H:%M:%S")

        diff = current_exp - old_exp
        if diff > 0:
            self.log(
                f"Account {acc['nickname']} ({uid_str}) gained +{diff} EXP! "
                f"Total Gained: +{acc['gained_exp']} | Mode: {self.get_account_mode(uid_str)}",
                "success", uid_str,
            )

        # Auto LIMIT status when exp_limit reached (skip if already PAUSED)
        if acc.get("status") != "PAUSED":
            limit = int(acc.get("exp_limit", 0) or 0)
            if limit > 0 and acc["gained_exp"] >= limit:
                if acc["status"] not in ("LIMIT", "PAUSED"):
                    acc["status"] = "LIMIT"
                    self.log(
                        f"Account {acc['nickname']} ({uid_str}) reached EXP limit "
                        f"({acc['gained_exp']}/{limit}). Standing by.",
                        "warning", uid_str,
                    )

        self.recalc_totals()

    # ---------- level / mode ----------
    def get_account_level(self, uid: str) -> int:
        acc = self.accounts.get(str(uid))
        return int(acc.get("level", 1) or 1) if acc else 1

    def get_account_mode(self, uid: str) -> str:
        """
        Effective mode:
          - Manual override always wins.
          - Auto: Level >= AUTO_LONE_WOLF_LEVEL (3) → LONE_WOLF.
          - Otherwise → BR.
        """
        acc = self.accounts.get(str(uid))
        if not acc:
            return "BR"

        mode_manual = bool(acc.get("mode_manual", False))
        saved = str(acc.get("mode", "BR") or "BR").upper()

        if mode_manual:
            return "LONE_WOLF" if saved in ("LONE_WOLF", "LONEWOLF", "LW") else "BR"

        level = int(acc.get("level", 1) or 1)
        if level >= AUTO_LONE_WOLF_LEVEL:
            return "LONE_WOLF"
        return "BR"

    def set_account_mode(self, uid: str, mode: str):
        uid_str = str(uid)
        mode = str(mode or "").upper().replace("-", "_").replace(" ", "_")
        if mode in ("LW", "LONEWOLF", "LONE_WOLF"):
            mode = "LONE_WOLF"
        elif mode == "BR":
            mode = "BR"
        else:
            return False, "Invalid mode"

        acc = self.accounts.get(uid_str)
        if not acc:
            return False, "Account not found"

        level = int(acc.get("level", 1) or 1)
        # Lone Wolf requires at least AUTO_LONE_WOLF_LEVEL
        if mode != "BR" and level < AUTO_LONE_WOLF_LEVEL:
            return False, f"Lone Wolf is locked until Level {AUTO_LONE_WOLF_LEVEL}"

        acc["mode"] = mode
        acc["mode_manual"] = True
        acc["last_updated"] = time.strftime("%H:%M:%S")
        self.log(
            f"UID {uid_str} ({acc.get('nickname', 'Unknown')}) switched to {mode}",
            "success", uid_str,
        )
        return True, mode

    # ---------- pause / resume ----------
    def is_paused(self, uid: str) -> bool:
        """Check whether an account is currently paused (worker should stand by)."""
        uid_str = str(uid)
        acc = self.accounts.get(uid_str)
        if not acc:
            return False
        return bool(acc.get("paused", False))

    def set_paused(self, uid: str, paused: bool):
        """Set pause state for an account. Returns (ok, error_or_None)."""
        uid_str = str(uid)
        acc = self.accounts.get(uid_str)
        if not acc:
            return False, "Account not found"
        acc["paused"] = bool(paused)
        acc["status"] = "PAUSED" if paused else "ONLINE"
        acc["last_updated"] = time.strftime("%H:%M:%S")
        self.log(
            f"UID {uid_str} ({acc.get('nickname', 'Unknown')}) "
            f"{'paused' if paused else 'resumed'}",
            "warning" if paused else "success",
            uid_str,
        )
        return True, None

    # ---------- status ----------
    def update_status(self, uid: str, status: str,
                      active_matches: Optional[int] = None):
        uid_str = str(uid)
        acc = self.accounts.get(uid_str)
        if not acc:
            return

        # Never overwrite PAUSED status while paused
        if acc.get("status") == "PAUSED" or acc.get("paused", False):
            if active_matches is not None:
                acc["active_matches"] = active_matches
            if status == "PAUSED":
                acc["status"] = "PAUSED"
                acc["last_updated"] = time.strftime("%H:%M:%S")
            return

        # Don't overwrite LIMIT with ONLINE while still over the limit
        if status == "ONLINE" and acc.get("status") == "LIMIT":
            limit = int(acc.get("exp_limit", 0) or 0)
            gained = int(acc.get("gained_exp", 0) or 0)
            if limit > 0 and gained >= limit:
                if active_matches is not None:
                    acc["active_matches"] = active_matches
                return

        acc["status"] = status
        if active_matches is not None:
            acc["active_matches"] = active_matches
        acc["last_updated"] = time.strftime("%H:%M:%S")

    # ---------- match count ----------
    def increment_match(self, uid: str):
        uid_str = str(uid)
        self.total_matches += 1
        if uid_str in self.accounts:
            self.accounts[uid_str]["matches_played"] += 1
            self.accounts[uid_str]["last_match_time"] = time.strftime("%H:%M:%S")
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")
            self.log(
                f"Account {self.accounts[uid_str]['nickname']} finished "
                f"Match #{self.accounts[uid_str]['matches_played']}",
                "info", uid_str,
            )

    def recalc_totals(self):
        self.total_gained_exp = sum(
            acc.get("gained_exp", 0) for acc in self.accounts.values()
        )


bot_state = BotState()

# Convenience cursor for the dashboard batch importer.
batch_import_state = {"last_start": 0, "last_count": 0, "last_end": 0, "total": 0}


# ==================== HELPERS ====================
def _enrich_account(acc: Dict[str, Any]) -> Dict[str, Any]:
    """Merge credential info (password, exp_limit, initial_exp, auth_uid...)
    into the account dict so the dashboard can display it."""
    uid = str(acc.get("uid", ""))
    cred = bot_state.account_credentials.get(uid, {}) or {}

    out = dict(acc)
    out["password"] = cred.get("auth_password", "") or ""
    out["auth_uid"] = cred.get("auth_uid", "") or ""
    out["auth_type"] = cred.get("auth_type", "guest")
    out["paused"] = bool(acc.get("paused", False))

    if "exp_limit" in cred:
        try:
            out["exp_limit"] = int(cred.get("exp_limit", 0) or 0)
        except (TypeError, ValueError):
            out["exp_limit"] = 0

    if "initial_exp" in cred:
        try:
            init = int(cred.get("initial_exp", 0) or 0)
            cur = int(cred.get("exp", out.get("current_exp", 0)) or 0)
            out["initial_exp"] = init
            out["current_exp"] = cur
            out["gained_exp"] = max(0, cur - init)
        except (TypeError, ValueError):
            pass

    return out


def _compute_effective_mode(acc: Dict[str, Any]) -> str:
    """Mirror BotState.get_account_mode for a raw dict."""
    mode_manual = bool(acc.get("mode_manual", False))
    saved = str(acc.get("mode", "BR") or "BR").upper()
    if mode_manual:
        return "LONE_WOLF" if saved in ("LONE_WOLF", "LONEWOLF", "LW") else "BR"
    level = int(acc.get("level", 1) or 1)
    if level >= AUTO_LONE_WOLF_LEVEL:
        return "LONE_WOLF"
    return "BR"


# ==================== HTTP HANDLERS ====================
TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")


async def handle_index(request: web.Request) -> web.Response:
    if os.path.exists(TEMPLATE_PATH):
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = "<h1>index.html not found!</h1>"
    return web.Response(text=content, content_type="text/html", charset="utf-8")


async def handle_get_stats(request: web.Request) -> web.Response:
    raw_accounts = list(bot_state.accounts.values())
    accounts_data = [_enrich_account(a) for a in raw_accounts]
    accounts_data.sort(key=lambda x: x.get("gained_exp", 0), reverse=True)

    # Add effective_mode for each account (frontend uses it directly)
    for enriched, raw in zip(accounts_data, raw_accounts):
        enriched["effective_mode"] = _compute_effective_mode(raw)

    uptime = max(1, int(time.time() - bot_state.start_time))
    exp_per_hour = int(bot_state.total_gained_exp * 3600 / uptime) if uptime > 0 else 0
    active_matches = sum(int(a.get("active_matches", 0) or 0) for a in raw_accounts)

    return web.json_response({
        "total_accounts": len(bot_state.accounts),
        "total_matches": bot_state.total_matches,
        "total_matches_started": bot_state.total_matches,
        "total_gained_exp": bot_state.total_gained_exp,
        "total_active_matches": active_matches,
        "exp_per_hour": exp_per_hour,
        "accounts": accounts_data,
        "logs": bot_state.logs[-60:],
        "uptime": uptime,
        "import_state": dict(batch_import_state),
    })


async def handle_account_detail(request: web.Request) -> web.Response:
    """Return a single account's enriched details (for the detail modal)."""
    try:
        uid = str(request.query.get("uid", "")).strip()
        if not uid:
            return web.json_response(
                {"status": "error", "error": "uid query param required"}, status=400
            )
        raw = bot_state.accounts.get(uid)
        if not raw:
            return web.json_response(
                {"status": "error", "error": "Account not found"}, status=404
            )
        enriched = _enrich_account(raw)
        enriched["effective_mode"] = _compute_effective_mode(raw)
        return web.json_response({"status": "ok", "account": enriched})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=400)


async def handle_add_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        accounts_file = "accounts.json"
        existing: List[Dict[str, Any]] = []
        if os.path.exists(accounts_file):
            try:
                with open(accounts_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                existing = []

        if "uid" in data and "password" in data:
            uid = str(data["uid"]).strip()
            pwd = str(data["password"]).strip()
            if not uid or not pwd:
                return web.json_response(
                    {"status": "error", "error": "UID and Password are required"}
                )
            existing = [acc for acc in existing if str(acc.get("uid")) != uid]
            existing.append({"uid": uid, "password": pwd})
        elif "token" in data:
            token = str(data["token"]).strip()
            if not token:
                return web.json_response(
                    {"status": "error", "error": "Token is required"}
                )
            existing = [acc for acc in existing if acc.get("token") != token]
            existing.append({"token": token})
        else:
            return web.json_response(
                {"status": "error", "error": "Invalid payload"}
            )

        with open(accounts_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)

        bot_state.log(f"New account added: {data.get('uid') or 'Token'}", "success")

        if "on_account_added" in bot_state.refresh_callbacks:
            asyncio.create_task(
                bot_state.refresh_callbacks["on_account_added"](data)
            )

        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_delete_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        auth_uid = str(data.get("auth_uid", "")).strip()
        token = str(data.get("token", "")).strip()
        accounts_file = "accounts.json"

        if os.path.exists(accounts_file):
            with open(accounts_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if token:
                existing = [acc for acc in existing
                            if str(acc.get("token", "")).strip() != token]
            elif auth_uid:
                existing = [acc for acc in existing
                            if str(acc.get("uid", "")).strip() != auth_uid]
            elif uid:
                existing = [acc for acc in existing
                            if str(acc.get("uid", "")).strip() != uid]
            with open(accounts_file, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)

        worker_keys = []
        if auth_uid:
            worker_keys.append(auth_uid)
        if uid:
            worker_keys.append(uid)
        if token:
            worker_keys.append(token[:10])

        for key in worker_keys:
            worker = bot_state.account_workers.get(key)
            if worker:
                if not worker.done():
                    worker.cancel()
                bot_state.account_workers.pop(key, None)

        if uid and uid in bot_state.accounts:
            del bot_state.accounts[uid]
        if auth_uid and auth_uid in bot_state.accounts:
            del bot_state.accounts[auth_uid]

        # Clean credentials
        for key in list(bot_state.account_credentials.keys()):
            cred = bot_state.account_credentials.get(key)
            if not cred:
                continue
            if (uid and str(cred.get("account_id", "")) == uid) or \
               (auth_uid and str(cred.get("auth_uid", "")) == auth_uid):
                del bot_state.account_credentials[key]

        bot_state.log(
            f"Account {uid or auth_uid or token[:10] or 'unknown'} removed from rotation.",
            "warning", uid or None,
        )
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def _load_accounts_file() -> List[Dict[str, Any]]:
    if not os.path.exists("accounts.json"):
        return []
    try:
        with open("accounts.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_accounts_file(accounts: List[Dict[str, Any]]) -> None:
    with open("accounts.json", "w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=2, ensure_ascii=False)


def _normalize_uploaded_accounts(payload: Any):
    if isinstance(payload, dict):
        for key in ("accounts", "data", "users"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        return [], "JSON must contain an account list (array or accounts/data/users array)"

    result: List[Dict[str, str]] = []
    for pos, item in enumerate(payload, 1):
        if not isinstance(item, dict):
            return [], f"Account #{pos} is not an object"
        uid = str(item.get("uid", "") or "").strip()
        password = str(item.get("password", "") or item.get("pass", "") or "").strip()
        token = str(item.get("token", "") or "").strip()
        if uid and password:
            result.append({"uid": uid, "password": password})
        elif token:
            result.append({"token": token})
        else:
            return [], f"Account #{pos} needs uid + password, or token"
    return result, None


async def handle_batch_import(request: web.Request) -> web.Response:
    global batch_import_state
    try:
        reader = await request.multipart()
        file_bytes = None
        file_filename = ""
        start_raw, count_raw = "1", "1"
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "file":
                file_filename = part.filename or ""
                file_bytes = await part.read(decode=False)
            elif part.name == "start":
                start_raw = (await part.text()).strip() or "1"
            elif part.name == "count":
                count_raw = (await part.text()).strip() or "1"

        if file_bytes is None or not file_filename:
            return web.json_response(
                {"status": "error", "error": "Please select a JSON file"}, status=400
            )
        try:
            start, count = int(start_raw), int(count_raw)
        except ValueError:
            return web.json_response(
                {"status": "error", "error": "Start and count must be whole numbers"},
                status=400,
            )
        if start < 1 or count < 1:
            return web.json_response(
                {"status": "error", "error": "Start and count must be >= 1"},
                status=400,
            )
        if count > 500:
            return web.json_response(
                {"status": "error", "error": "Maximum 500 accounts per batch"},
                status=400,
            )

        raw = file_bytes
        if len(raw) > 2 * 1024 * 1024:
            return web.json_response(
                {"status": "error", "error": "JSON file is too large (max 2 MB)"},
                status=400,
            )
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except Exception as e:
            return web.json_response(
                {"status": "error", "error": f"Invalid JSON: {e}"}, status=400
            )

        source, err = _normalize_uploaded_accounts(payload)
        if err:
            return web.json_response({"status": "error", "error": err}, status=400)
        total = len(source)
        if total == 0:
            return web.json_response(
                {"status": "error", "error": "JSON contains no accounts"}, status=400
            )
        if start > total:
            return web.json_response(
                {"status": "error",
                 "error": f"Start account #{start} is outside the JSON (total {total})"},
                status=400,
            )

        end = min(total, start + count - 1)
        selected = source[start - 1:end]
        existing = await _load_accounts_file()
        for acc in selected:
            if "uid" in acc:
                existing = [x for x in existing
                            if str(x.get("uid", "")).strip() != acc["uid"]]
            else:
                existing = [x for x in existing
                            if str(x.get("token", "")).strip() != acc["token"]]
            existing.append(acc)
        _save_accounts_file(existing)

        batch_import_state = {
            "last_start": start, "last_count": len(selected),
            "last_end": end, "total": total,
        }

        callback = bot_state.refresh_callbacks.get("on_account_added")
        started = 0
        if callback:
            for acc in selected:
                await callback(acc)
                started += 1
                if started < len(selected):
                    await asyncio.sleep(0.20)

        bot_state.log(
            f"Batch import: accounts {start}-{end} selected from {total}; "
            f"{len(selected)} loaded.",
            "success",
        )
        return web.json_response({
            "status": "ok",
            "total": total,
            "start": start,
            "count": len(selected),
            "end": end,
            "next_start": end + 1 if end < total else None,
            "started": started,
            "message": f"Accounts {start}-{end} imported",
        })
    except Exception as e:
        return web.json_response(
            {"status": "error", "error": str(e)}, status=500
        )


async def handle_delete_all_accounts(request: web.Request) -> web.Response:
    try:
        accounts = await _load_accounts_file()
        removed = len(accounts)
        workers = list(bot_state.account_workers.values())
        for worker in workers:
            if worker and not worker.done():
                worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        bot_state.account_workers.clear()
        _save_accounts_file([])
        bot_state.accounts.clear()
        bot_state.account_credentials.clear()
        bot_state.recalc_totals()
        bot_state.log(
            f"All accounts deleted. {removed} account(s) removed from rotation.",
            "warning",
        )
        return web.json_response({"status": "ok", "removed": removed})
    except Exception as e:
        return web.json_response(
            {"status": "error", "error": str(e)}, status=500
        )


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({
        "status": "ok",
        "accounts": len(bot_state.accounts),
        "uptime": int(time.time() - bot_state.start_time),
    })


async def handle_refresh_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid")).strip()
        if "on_refresh_account" in bot_state.refresh_callbacks:
            asyncio.create_task(
                bot_state.refresh_callbacks["on_refresh_account"](uid)
            )
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_mode_switch(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        mode = str(data.get("mode", "")).strip()
        ok, result = bot_state.set_account_mode(uid, mode)
        if not ok:
            return web.json_response(
                {"status": "error", "error": result}, status=400
            )
        return web.json_response({
            "status": "ok",
            "uid": uid,
            "mode": result,
            "level": bot_state.get_account_level(uid),
            "auto_level": AUTO_LONE_WOLF_LEVEL,
        })
    except Exception as e:
        return web.json_response(
            {"status": "error", "error": str(e)}, status=400
        )


async def handle_set_exp_limit(request: web.Request) -> web.Response:
    """Set / update per-account EXP limit."""
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        if not uid:
            return web.json_response(
                {"status": "error", "error": "UID is required"}, status=400
            )
        try:
            limit = max(0, int(data.get("limit", 0)))
        except (TypeError, ValueError):
            return web.json_response(
                {"status": "error", "error": "limit must be a non-negative integer"},
                status=400,
            )

        # Preferred: delegate to main.py callback which keeps everything in sync.
        callback = bot_state.refresh_callbacks.get("on_set_limit")
        if callback:
            result = await callback(uid, limit)
            return web.json_response(
                result or {"status": "ok", "uid": uid, "limit": limit}
            )

        # Fallback: update local state.
        if uid in bot_state.accounts:
            bot_state.accounts[uid]["exp_limit"] = limit
            bot_state.accounts[uid]["last_updated"] = time.strftime("%H:%M:%S")
        cred = bot_state.account_credentials.get(uid)
        if cred:
            cred["exp_limit"] = limit
            cred.setdefault("initial_exp", cred.get("exp", 0))

        # If the limit was raised/cleared, allow the account to leave LIMIT state.
        if uid in bot_state.accounts:
            acc = bot_state.accounts[uid]
            gained = int(acc.get("gained_exp", 0) or 0)
            if limit == 0 or gained < limit:
                if acc.get("status") == "LIMIT":
                    acc["status"] = "PAUSED" if acc.get("paused") else "ONLINE"

        bot_state.log(
            f"EXP limit for UID {uid} set to {limit if limit > 0 else 'unlimited'}",
            "success", uid,
        )
        return web.json_response({"status": "ok", "uid": uid, "limit": limit})
    except Exception as e:
        return web.json_response(
            {"status": "error", "error": str(e)}, status=400
        )


async def handle_pause_account(request: web.Request) -> web.Response:
    """Pause / resume a single account worker."""
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        if not uid:
            return web.json_response(
                {"status": "error", "error": "UID is required"}, status=400
            )
        paused = bool(data.get("paused", True))
        ok, err = bot_state.set_paused(uid, paused)
        if not ok:
            return web.json_response(
                {"status": "error", "error": err or "Account not found"}, status=404
            )
        return web.json_response({
            "status": "ok", "uid": uid, "paused": paused
        })
    except Exception as e:
        return web.json_response(
            {"status": "error", "error": str(e)}, status=400
        )


# ==================== SERVER BOOT ====================
async def start_web_dashboard(host: str = "0.0.0.0", port: int = 5000):
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/stats", handle_get_stats)
    app.router.add_post("/api/account/add", handle_add_account)
    app.router.add_post("/api/account/delete", handle_delete_account)
    app.router.add_post("/api/account/import", handle_batch_import)
    app.router.add_post("/api/account/delete-all", handle_delete_all_accounts)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/api/account/refresh", handle_refresh_account)
    app.router.add_post("/api/account/mode", handle_mode_switch)
    app.router.add_post("/api/account/set-limit", handle_set_exp_limit)
    app.router.add_get("/api/account/detail", handle_account_detail)
    app.router.add_post("/api/account/pause", handle_pause_account)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    print(f"\033[92m[+] Web Dashboard running on http://localhost:{port}\033[0m")
    print(f"\033[96m[i] Auto Lone Wolf activates at Level {AUTO_LONE_WOLF_LEVEL}+\033[0m")