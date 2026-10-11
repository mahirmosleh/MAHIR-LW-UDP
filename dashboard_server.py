# -*- coding: utf-8 -*-
"""
FreeFire Level Up Bot - Professional Web Dashboard & Real-Time EXP Tracker
Embedded Async Web Server (aiohttp) - Single File Edition
Supports LW / BR / AUTO mode selection per account
"""

import asyncio
import json
import os
import time
from typing import Dict, List, Any, Optional
from aiohttp import web


# ============================================================
#  EXP TABLE + LEVEL CALCULATION
# ============================================================

EXP_TABLE: Dict[int, int] = {
    1: 0, 2: 48, 3: 202, 4: 544, 5: 1012, 6: 1844, 7: 2792, 8: 3800,
    9: 4870, 10: 6004, 11: 7192, 12: 8448, 13: 9760, 14: 11140, 15: 12566,
    16: 14060, 17: 15610, 18: 17224, 19: 18902, 20: 20632, 21: 22424, 22: 24278,
    23: 26192, 24: 28166, 25: 30200, 26: 32294, 27: 34448, 28: 37804, 29: 41274,
    30: 44870, 31: 48582, 32: 53394, 33: 58566, 34: 64096, 35: 69994, 36: 76460,
    37: 83506, 38: 91128, 39: 99322, 40: 108092, 41: 120144, 42: 133266, 43: 147472,
    44: 162760, 45: 179126, 46: 196572, 47: 215368, 48: 235516, 49: 257010, 50: 279860,
    51: 304056, 52: 348318, 53: 394982, 54: 444044, 55: 495508, 56: 549364, 57: 633756,
    58: 721744, 59: 813336, 60: 908522, 61: 1041438, 62: 1180352, 63: 1325266,
    64: 1476184, 65: 1634300, 66: 1840946, 67: 2056594, 68: 2281242, 69: 2514880,
    70: 2757530, 71: 3059506, 72: 3372284, 73: 3699456, 74: 4041030, 75: 4397002,
    76: 4829104, 77: 5282204, 78: 5756304, 79: 6251408, 80: 6776502, 81: 7381324,
    82: 8043154, 83: 8752982, 84: 9510808, 85: 10316338, 86: 11277190, 87: 12291748,
    88: 13360304, 89: 14482858, 90: 15659418, 91: 17026708, 92: 18453950, 93: 19941280,
    94: 21488570, 95: 23095858, 96: 24763138, 97: 26490428, 98: 28378704, 99: 30124996,
    100: 32032884
}


def calculate_level_progress(level: int, current_exp: int) -> Dict[str, Any]:
    level = max(1, level)
    next_level = min(100, level + 1)
    base_exp = EXP_TABLE.get(level, 0)
    target_exp = EXP_TABLE.get(next_level, base_exp + 50000)

    needed_for_level = max(1, target_exp - base_exp)
    earned_in_level = max(0, current_exp - base_exp)
    remaining_exp = max(0, target_exp - current_exp)
    progress_pct = min(100.0, max(0.0, (earned_in_level / needed_for_level) * 100.0))

    return {
        "next_level": next_level,
        "base_exp": base_exp,
        "target_exp": target_exp,
        "needed_for_level": needed_for_level,
        "earned_in_level": earned_in_level,
        "remaining_exp": remaining_exp,
        "progress_pct": round(progress_pct, 1)
    }


# ============================================================
#  BOT STATE
# ============================================================

class BotState:
    def __init__(self):
        self.accounts: Dict[str, Dict[str, Any]] = {}
        self.logs: List[Dict[str, Any]] = []
        self.max_logs = 200
        self.total_matches = 0
        self.total_gained_exp = 0
        self.start_time = time.time()
        self.account_workers: Dict[str, asyncio.Task] = {}
        self.account_token_map: Dict[str, str] = {}
        self.auth_to_game_id: Dict[str, str] = {}
        self.game_to_auth_id: Dict[str, str] = {}
        self.paused_accounts: set = set()
        self.refresh_callbacks: Dict[str, Any] = {}
        self.account_credentials: Dict[str, Dict[str, Any]] = {}
        self.active_writers: Dict[str, set] = {}
        # uid -> 'LW' | 'BR' | 'AUTO'
        self.account_mode_preferences: Dict[str, str] = {}

    # ---------- MODE PREFERENCE ----------
    def set_account_mode_preference(self, uid: str, mode: str):
        uid_str = str(uid)
        mode = (mode or "AUTO").upper()
        if mode not in ("LW", "BR", "AUTO"):
            mode = "AUTO"
        self.account_mode_preferences[uid_str] = mode
        if uid_str in self.accounts:
            self.accounts[uid_str]["mode_preference"] = mode

    def get_account_mode_preference(self, uid: str) -> str:
        uid_str = str(uid)
        if uid_str in self.account_mode_preferences:
            return self.account_mode_preferences[uid_str]
        acc = self.accounts.get(uid_str)
        if acc and acc.get("mode_preference"):
            return str(acc["mode_preference"])
        return "AUTO"

    def get_account_level(self, uid: str) -> int:
        uid_str = str(uid)
        acc = self.accounts.get(uid_str)
        if not acc:
            mapped = self.game_to_auth_id.get(uid_str) or self.auth_to_game_id.get(uid_str)
            if mapped and mapped in self.accounts:
                acc = self.accounts[mapped]
        if acc:
            return int(acc.get("level", 1) or 1)
        return 1

    # ---------- WRITERS ----------
    def register_writer(self, uid: str, writer):
        uid_str = str(uid)
        if uid_str not in self.active_writers:
            self.active_writers[uid_str] = set()
        self.active_writers[uid_str].add(writer)

    def unregister_writer(self, uid: str, writer):
        uid_str = str(uid)
        if uid_str in self.active_writers:
            self.active_writers[uid_str].discard(writer)
            if not self.active_writers[uid_str]:
                self.active_writers.pop(uid_str, None)

    def close_writers_for_account(self, uid: str):
        uid_str = str(uid)
        candidates = {uid_str}
        if uid_str in self.auth_to_game_id:
            candidates.add(str(self.auth_to_game_id[uid_str]))
        if uid_str in self.game_to_auth_id:
            candidates.add(str(self.game_to_auth_id[uid_str]))
        if uid_str in self.account_token_map:
            mapped = self.account_token_map[uid_str]
            candidates.add(str(mapped))
            candidates.add(str(mapped)[:16])

        for c in list(candidates):
            writers = list(self.active_writers.get(c, []))
            for w in writers:
                try:
                    if hasattr(w, "close"):
                        if hasattr(w, "is_closing"):
                            if not w.is_closing():
                                w.close()
                        else:
                            w.close()
                except Exception:
                    pass
            self.active_writers.pop(c, None)

    # ---------- LOGS ----------
    def log(self, message: str, level: str = "info", uid: Optional[str] = None):
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "level": level,
            "message": message,
            "uid": str(uid) if uid else None
        }
        self.logs.append(entry)
        if len(self.logs) > self.max_logs:
            self.logs.pop(0)

    # ---------- ACCOUNTS ----------
    def register_account(self, uid: str, nickname: str, region: str, level: int, exp: int,
                         likes: int = 0, token: Optional[str] = None, auth_uid: Optional[str] = None):
        uid_str = str(uid)
        auth_uid_str = str(auth_uid) if auth_uid else self.game_to_auth_id.get(uid_str, "")
        if auth_uid_str:
            self.auth_to_game_id[auth_uid_str] = uid_str
            self.game_to_auth_id[uid_str] = auth_uid_str
            self.account_token_map[auth_uid_str] = uid_str
            self.account_token_map[uid_str] = auth_uid_str
        if token:
            self.account_token_map[uid_str] = token
            self.account_token_map[token[:16]] = uid_str
            if auth_uid_str:
                self.account_token_map[auth_uid_str] = token

        prog = calculate_level_progress(level or 1, exp)

        lvl_val = level or 1
        acc_mode = "BR" if lvl_val < 3 else "LONE_WOLF"
        acc_mode_label = "Battle Royale (Lvl < 3)" if lvl_val < 3 else "Lone Wolf (Lvl 3+)"

        if uid_str not in self.accounts:
            self.accounts[uid_str] = {
                "uid": uid_str,
                "auth_uid": auth_uid_str or "",
                "nickname": nickname or f"Player_{uid_str[:6]}",
                "region": region or "BD",
                "level": lvl_val,
                "next_level": prog["next_level"],
                "mode": acc_mode,
                "mode_label": acc_mode_label,
                "mode_preference": self.account_mode_preferences.get(uid_str, "AUTO"),
                "initial_exp": exp,
                "current_exp": exp,
                "gained_exp": 0,
                "remaining_exp": prog["remaining_exp"],
                "target_exp": prog["target_exp"],
                "needed_for_level": prog["needed_for_level"],
                "earned_in_level": prog["earned_in_level"],
                "progress_pct": prog["progress_pct"],
                "likes": likes or 0,
                "status": "PAUSED" if self.is_paused(uid_str) else "ONLINE",
                "matches_played": 0,
                "active_matches": 0,
                "last_match_time": None,
                "token": token or "",
                "start_time": time.time(),
                "is_paused": self.is_paused(uid_str),
                "paused_at": time.time() if self.is_paused(uid_str) else None,
                "total_pause_duration": 0.0,
                "last_updated": time.strftime("%H:%M:%S")
            }
        else:
            acc = self.accounts[uid_str]
            if auth_uid_str:
                acc["auth_uid"] = auth_uid_str
            if nickname:
                acc["nickname"] = nickname
            if region:
                acc["region"] = region
            if level:
                acc["level"] = level
            if token:
                acc["token"] = token
            acc["current_exp"] = exp
            acc["gained_exp"] = max(0, exp - acc["initial_exp"])
            acc["next_level"] = prog["next_level"]
            acc["remaining_exp"] = prog["remaining_exp"]
            acc["target_exp"] = prog["target_exp"]
            acc["needed_for_level"] = prog["needed_for_level"]
            acc["earned_in_level"] = prog["earned_in_level"]
            acc["progress_pct"] = prog["progress_pct"]
            acc["likes"] = likes
            acc["mode_preference"] = self.account_mode_preferences.get(uid_str, acc.get("mode_preference", "AUTO"))
            if not acc.get("is_paused"):
                acc["status"] = "ONLINE"
            acc["last_updated"] = time.strftime("%H:%M:%S")
        self.recalc_totals()

    def get_account_uptime(self, uid_str: str) -> int:
        acc = self.accounts.get(uid_str)
        if not acc:
            mapped = self.game_to_auth_id.get(uid_str) or self.auth_to_game_id.get(uid_str)
            if mapped and mapped in self.accounts:
                acc = self.accounts[mapped]
        if not acc:
            return 0
        start_t = acc.get("start_time", time.time())
        total_pause = acc.get("total_pause_duration", 0.0)
        if acc.get("is_paused") and acc.get("paused_at"):
            return max(0, int(acc["paused_at"] - start_t - total_pause))
        return max(0, int(time.time() - start_t - total_pause))

    def is_paused(self, uid: str) -> bool:
        uid_str = str(uid)
        if uid_str in self.paused_accounts:
            return True
        game_id = self.auth_to_game_id.get(uid_str)
        if game_id and game_id in self.paused_accounts:
            return True
        auth_uid = self.game_to_auth_id.get(uid_str)
        if auth_uid and auth_uid in self.paused_accounts:
            return True
        acc = self.accounts.get(uid_str) or (self.accounts.get(game_id) if game_id else None)
        if acc and acc.get("is_paused"):
            return True
        return False

    def toggle_pause(self, uid: str) -> bool:
        uid_str = str(uid)
        candidates = {uid_str}
        if uid_str in self.auth_to_game_id:
            candidates.add(self.auth_to_game_id[uid_str])
        if uid_str in self.game_to_auth_id:
            candidates.add(self.game_to_auth_id[uid_str])

        target_acc = None
        target_key = uid_str
        for c in candidates:
            if c in self.accounts:
                target_acc = self.accounts[c]
                target_key = c
                break

        is_now_paused = not self.is_paused(uid_str)
        if is_now_paused:
            for c in candidates:
                self.paused_accounts.add(c)
                self.close_writers_for_account(c)
            if target_acc:
                target_acc["is_paused"] = True
                target_acc["paused_at"] = time.time()
                target_acc["status"] = "PAUSED"
            nick = target_acc.get("nickname", target_key) if target_acc else target_key
            self.log(f"⏸ UID {target_key} ({nick}) matchmaking PAUSED.", "warning", target_key)
            if "on_pause_toggle" in self.refresh_callbacks:
                try:
                    asyncio.create_task(self.refresh_callbacks["on_pause_toggle"](target_key, True))
                except Exception:
                    pass
        else:
            for c in candidates:
                self.paused_accounts.discard(c)
            if target_acc:
                target_acc["is_paused"] = False
                if target_acc.get("paused_at"):
                    pause_dur = time.time() - target_acc["paused_at"]
                    target_acc["total_pause_duration"] = target_acc.get("total_pause_duration", 0.0) + pause_dur
                    target_acc["paused_at"] = None
                target_acc["status"] = "ONLINE"
            nick = target_acc.get("nickname", target_key) if target_acc else target_key
            self.log(f"▶ UID {target_key} ({nick}) matchmaking RESUMED.", "success", target_key)
            if "on_pause_toggle" in self.refresh_callbacks:
                try:
                    asyncio.create_task(self.refresh_callbacks["on_pause_toggle"](target_key, False))
                except Exception:
                    pass

        return is_now_paused

    def toggle_pause_all(self) -> bool:
        any_active = any(not self.is_paused(k) for k in self.accounts.keys())
        for k in list(self.accounts.keys()):
            current_paused = self.is_paused(k)
            if any_active and not current_paused:
                self.toggle_pause(k)
            elif not any_active and current_paused:
                self.toggle_pause(k)
        return any_active

    def update_exp(self, uid: str, current_exp: int, level: Optional[int] = None):
        uid_str = str(uid)
        if uid_str in self.accounts:
            acc = self.accounts[uid_str]
            old_exp = acc["current_exp"]
            old_level = acc.get("level", 1)
            acc["current_exp"] = current_exp
            if level is not None and level > 0:
                acc["level"] = level
            acc["gained_exp"] = max(0, current_exp - acc["initial_exp"])

            current_lvl = acc["level"]
            acc["mode"] = "BR" if current_lvl < 3 else "LONE_WOLF"
            acc["mode_label"] = "Battle Royale (Lvl < 3)" if current_lvl < 3 else "Lone Wolf (Lvl 3+)"

            prog = calculate_level_progress(acc["level"], current_exp)
            acc["next_level"] = prog["next_level"]
            acc["remaining_exp"] = prog["remaining_exp"]
            acc["target_exp"] = prog["target_exp"]
            acc["needed_for_level"] = prog["needed_for_level"]
            acc["earned_in_level"] = prog["earned_in_level"]
            acc["progress_pct"] = prog["progress_pct"]
            acc["last_updated"] = time.strftime("%H:%M:%S")

            if old_level < 3 and current_lvl >= 3:
                self.log(
                    f"🎉 LEVEL UP! UID {uid_str} ({acc['nickname']}) reached Level {current_lvl}!",
                    "success",
                    uid_str
                )

            diff = current_exp - old_exp
            if diff > 0:
                self.log(
                    f"★ UID {uid_str} ({acc['nickname']}) gained +{diff:,} EXP | Level {acc['level']} [{acc['mode']}] ({prog['progress_pct']}% - {prog['remaining_exp']:,} EXP to Lvl {prog['next_level']})",
                    "success",
                    uid_str
                )
            self.recalc_totals()

    def update_status(self, uid: str, status: str, active_matches: Optional[int] = None):
        uid_str = str(uid)
        if uid_str in self.accounts:
            self.accounts[uid_str]["status"] = status
            if active_matches is not None:
                self.accounts[uid_str]["active_matches"] = active_matches
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")

    def increment_match(self, uid: str):
        uid_str = str(uid)
        self.total_matches += 1
        if uid_str in self.accounts:
            self.accounts[uid_str]["matches_played"] += 1
            self.accounts[uid_str]["last_match_time"] = time.strftime("%H:%M:%S")
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")
            self.log(f"⚔ Match #{self.accounts[uid_str]['matches_played']} finished for {self.accounts[uid_str]['nickname']} ({uid_str})", "info", uid_str)

    def recalc_totals(self):
        self.total_gained_exp = sum(acc.get("gained_exp", 0) for acc in self.accounts.values())


bot_state = BotState()


# ============================================================
#  EMBEDDED HTML DASHBOARD
# ============================================================

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>M4MUUU | FreeFire Ultra Level Up Command Center</title>

    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Outfit:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.2/css/all.min.css">

    <style>
        :root {
            --bg-base: #08060f;
            --bg-card: rgba(20, 16, 40, 0.82);
            --bg-card-hover: rgba(32, 24, 60, 0.94);
            --bg-glass-heavy: rgba(14, 10, 30, 0.92);
            --border-card: rgba(168, 85, 247, 0.18);
            --border-hover: rgba(0, 255, 200, 0.45);

            --accent-cyan: #00ffc8;
            --accent-blue: #00b3ff;
            --accent-purple: #a855f7;
            --accent-green: #00ffc8;
            --accent-emerald: #22ffb3;
            --accent-red: #ff2e63;
            --accent-amber: #ff9f1c;

            --text-main: #f0f4ff;
            --text-muted: #9aa5c9;
            --text-dim: #6b739a;

            --glow-cyan: 0 0 26px rgba(0, 255, 200, 0.28);
            --shadow-card: 0 12px 32px -6px rgba(0, 0, 0, 0.75);
            --radius-lg: 20px;
            --radius-md: 14px;
            --radius-sm: 8px;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Outfit', sans-serif; -webkit-tap-highlight-color: transparent; }

        body {
            background-color: var(--bg-base);
            background-image:
                radial-gradient(circle at 10% 12%, rgba(168, 85, 247, 0.18) 0%, transparent 46%),
                radial-gradient(circle at 88% 88%, rgba(0, 255, 200, 0.13) 0%, transparent 52%),
                linear-gradient(180deg, #08060f 0%, #120e28 55%, #0a0818 100%);
            color: var(--text-main);
            min-height: 100vh;
            overflow-x: hidden;
            background-attachment: fixed;
        }

        .app-shell { position: relative; z-index: 1; max-width: 1520px; margin: 0 auto; padding: 24px; }

        .btn, .icon-btn, .btn-copy, .filter-btn, .console-pill, .toggle-btn, .btn-pause-all,
        .mobile-nav-item, .brand-logo, .stat-card, .acc-card, .form-control {
            transition: transform 0.18s cubic-bezier(0.34, 1.56, 0.64, 1),
                        box-shadow 0.25s ease, background 0.25s ease,
                        border-color 0.25s ease, color 0.2s ease, opacity 0.2s ease;
            will-change: transform;
        }

        .btn:active, .icon-btn:active, .btn-copy:active,
        .filter-btn:active, .console-pill:active, .toggle-btn:active, .btn-pause-all:active, .mobile-nav-item:active {
            transform: scale(0.90);
            transition: transform 0.08s ease;
        }

        /* ==================== DESKTOP HEADER ==================== */
        .desktop-header {
            display: flex; align-items: center; justify-content: space-between;
            padding: 18px 28px; background: var(--bg-card);
            border: 1px solid var(--border-card);
            border-radius: var(--radius-lg);
            backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
            box-shadow: var(--shadow-card); margin-bottom: 24px;
        }

        .brand { display: flex; align-items: center; gap: 16px; }

        .brand-logo {
            width: 50px; height: 50px;
            background: linear-gradient(135deg, #a855f7 0%, #00b3ff 45%, #00ffc8 100%);
            border-radius: 14px; display: flex; align-items: center; justify-content: center;
            font-size: 24px; color: #fff;
            box-shadow: 0 0 22px rgba(168, 85, 247, 0.55);
            animation: pulse-glow 3s infinite alternate;
            cursor: pointer;
        }

        @keyframes pulse-glow {
            0% { box-shadow: 0 0 16px rgba(168, 85, 247, 0.45); }
            100% { box-shadow: 0 0 28px rgba(0, 255, 200, 0.55); }
        }

        .brand-title h1 {
            font-size: 23px; font-weight: 800; letter-spacing: -0.5px;
            background: linear-gradient(90deg, #ffffff 15%, #c084fc 55%, #00ffc8 100%);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
        }

        .brand-sub { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--text-muted); margin-top: 3px; }

        .badge-live {
            display: inline-flex; align-items: center; gap: 6px;
            background: rgba(0, 255, 200, 0.12);
            border: 1px solid rgba(0, 255, 200, 0.35);
            color: var(--accent-cyan); padding: 3px 10px; border-radius: 20px;
            font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
        }

        .radar-dot { position: relative; width: 8px; height: 8px; background: var(--accent-cyan); border-radius: 50%; }
        .radar-dot::after {
            content: ''; position: absolute; top: -4px; left: -4px; right: -4px; bottom: -4px;
            border: 2px solid var(--accent-cyan); border-radius: 50%;
            animation: radar-wave 1.6s ease-out infinite;
        }
        @keyframes radar-wave {
            0% { transform: scale(0.6); opacity: 1; }
            100% { transform: scale(2.2); opacity: 0; }
        }

        .header-controls { display: flex; align-items: center; gap: 12px; }

        .btn {
            display: inline-flex; align-items: center; justify-content: center; gap: 8px;
            padding: 10px 20px; border-radius: var(--radius-md); font-size: 14px; font-weight: 600;
            cursor: pointer; border: 1px solid transparent; outline: none; user-select: none;
        }

        .btn-primary {
            background: linear-gradient(135deg, #a855f7 0%, #00b3ff 55%, #00ffc8 100%);
            color: #071018; font-weight: 800;
            box-shadow: 0 4px 18px rgba(168, 85, 247, 0.4);
        }

        .btn-primary:hover { transform: translateY(-2px); }

        .btn-secondary {
            background: rgba(255, 255, 255, 0.05);
            border-color: rgba(168, 85, 247, 0.25);
            color: var(--text-main);
        }

        .btn-secondary:hover { background: rgba(168, 85, 247, 0.12); border-color: var(--border-hover); }

        /* ==================== STATS HERO ==================== */
        .stats-hero { display: grid; grid-template-columns: repeat(4, 1fr); gap: 18px; margin-bottom: 24px; }

        .stat-card {
            background: var(--bg-card); border: 1px solid var(--border-card);
            border-radius: var(--radius-lg); padding: 20px 22px;
            backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
            box-shadow: var(--shadow-card);
            display: flex; align-items: center; justify-content: space-between;
            position: relative; overflow: hidden; cursor: pointer;
        }

        .stat-card::before {
            content: ''; position: absolute; top: 0; left: 0; width: 4px; height: 100%;
            background: transparent; transition: background 0.3s ease;
        }

        .stat-card:hover { transform: translateY(-3px); border-color: var(--border-hover); box-shadow: var(--glow-cyan); }

        .stat-card.cyan::before { background: var(--accent-cyan); }
        .stat-card.green::before { background: var(--accent-green); }
        .stat-card.purple::before { background: var(--accent-purple); }
        .stat-card.amber::before { background: var(--accent-amber); }

        .stat-data p { font-size: 13px; color: var(--text-muted); font-weight: 500; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.6px; }
        .stat-data h2 { font-size: 28px; font-weight: 800; letter-spacing: -0.5px; color: #fff; font-family: 'JetBrains Mono', monospace; }

        .stat-badge { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 6px; margin-top: 6px; }
        .stat-badge.green { background: rgba(0, 255, 200, 0.14); color: var(--accent-cyan); }
        .stat-badge.cyan { background: rgba(0, 255, 200, 0.12); color: var(--accent-cyan); }
        .stat-badge.purple { background: rgba(168, 85, 247, 0.15); color: #c084fc; }

        .stat-icon-wrap {
            width: 54px; height: 54px; border-radius: 16px;
            display: flex; align-items: center; justify-content: center; font-size: 22px;
        }
        .stat-icon-wrap.cyan { background: rgba(0, 255, 200, 0.13); color: var(--accent-cyan); }
        .stat-icon-wrap.green { background: rgba(0, 255, 200, 0.13); color: var(--accent-cyan); }
        .stat-icon-wrap.purple { background: rgba(168, 85, 247, 0.15); color: #d8b4fe; }
        .stat-icon-wrap.amber { background: rgba(255, 159, 28, 0.15); color: var(--accent-amber); }

        /* ==================== WORKSPACE ==================== */
        .workspace-grid { display: grid; grid-template-columns: 1fr 480px; gap: 24px; }

        .panel-box {
            background: var(--bg-card); border: 1px solid var(--border-card);
            border-radius: var(--radius-lg); padding: 22px;
            backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
            box-shadow: var(--shadow-card);
            display: flex; flex-direction: column;
        }

        .panel-header {
            display: flex; align-items: center; justify-content: space-between;
            margin-bottom: 20px; padding-bottom: 14px;
            border-bottom: 1px solid rgba(168, 85, 247, 0.12);
            flex-wrap: wrap; gap: 12px;
        }

        .panel-title { display: flex; align-items: center; gap: 12px; }
        .panel-title h3 { font-size: 18px; font-weight: 700; color: #fff; }
        .panel-title .counter-pill {
            background: rgba(0, 255, 200, 0.13); color: var(--accent-cyan);
            border: 1px solid rgba(0, 255, 200, 0.25); padding: 2px 10px;
            border-radius: 12px; font-size: 12px; font-weight: 700;
            font-family: 'JetBrains Mono', monospace;
        }

        .panel-actions { display: flex; align-items: center; gap: 10px; }

        .filter-row { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 18px; flex-wrap: wrap; }

        .search-box { flex: 1; min-width: 220px; position: relative; }
        .search-box i {
            position: absolute; left: 14px; top: 50%; transform: translateY(-50%);
            color: var(--text-dim); font-size: 13px;
        }
        .search-box input {
            width: 100%; background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(168, 85, 247, 0.2);
            border-radius: var(--radius-md); padding: 9px 14px 9px 38px;
            color: #fff; font-size: 13px; outline: none;
        }
        .search-box input:focus { border-color: rgba(0, 255, 200, 0.55); background: rgba(0, 255, 200, 0.07); }

        .filter-tabs {
            display: flex; gap: 6px; background: rgba(0, 0, 0, 0.35);
            padding: 4px; border-radius: var(--radius-md);
            border: 1px solid rgba(168, 85, 247, 0.15);
        }
        .filter-btn {
            background: transparent; border: none; color: var(--text-muted);
            padding: 6px 12px; border-radius: 8px; font-size: 12px; font-weight: 600; cursor: pointer;
        }
        .filter-btn.active { background: rgba(0, 255, 200, 0.16); color: var(--accent-cyan); border: 1px solid rgba(0, 255, 200, 0.28); }

        .accounts-grid {
            display: flex; flex-direction: column; gap: 16px;
            max-height: 680px; overflow-y: auto; padding-right: 4px;
        }
        .accounts-grid::-webkit-scrollbar { width: 5px; }
        .accounts-grid::-webkit-scrollbar-thumb { background: rgba(168, 85, 247, 0.25); border-radius: 4px; }

        /* ==================== ACCOUNT CARD ==================== */
        .acc-card {
            background: rgba(12, 10, 28, 0.6);
            border: 1px solid rgba(168, 85, 247, 0.15);
            border-radius: 16px; padding: 18px 20px;
            position: relative; cursor: default;
        }

        .acc-card:hover { background: var(--bg-card-hover); border-color: var(--border-hover); transform: translateY(-2px); }

        .acc-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; flex-wrap: wrap; gap: 10px; }
        .acc-identity { display: flex; align-items: center; gap: 12px; }

        .acc-avatar {
            width: 44px; height: 44px; border-radius: 12px;
            background: linear-gradient(135deg, #1a1438, #2a1e5a);
            display: flex; align-items: center; justify-content: center;
            font-size: 18px; color: var(--accent-cyan);
            border: 1px solid rgba(0, 255, 200, 0.3);
            position: relative;
        }

        .level-badge {
            position: absolute; bottom: -5px; right: -5px;
            background: #0f0a1f; border: 1px solid var(--accent-cyan);
            color: #fff; font-size: 9px; font-weight: 800;
            padding: 1px 4px; border-radius: 6px;
        }

        .acc-details h4 { font-size: 16px; font-weight: 700; color: #fff; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

        .region-tag {
            background: rgba(0, 179, 255, 0.16); color: #66d9ff;
            border: 1px solid rgba(0, 179, 255, 0.35);
            padding: 1px 6px; border-radius: 5px;
            font-size: 10px; font-weight: 700;
        }

        .acc-uid-row { display: flex; align-items: center; gap: 6px; margin-top: 2px; flex-wrap: wrap; }
        .acc-uid { font-size: 12px; color: var(--text-muted); font-family: 'JetBrains Mono', monospace; }

        .btn-copy {
            background: transparent; border: none; color: var(--text-dim);
            cursor: pointer; font-size: 11px; padding: 2px 4px; border-radius: 4px;
        }
        .btn-copy:hover { color: var(--accent-cyan); background: rgba(0, 255, 200, 0.1); }

        .acc-uptime-pill {
            display: inline-flex; align-items: center; gap: 5px;
            font-size: 11px; font-family: 'JetBrains Mono', monospace;
            font-weight: 600; color: #00ffc8;
            background: rgba(0, 255, 200, 0.10);
            border: 1px solid rgba(0, 255, 200, 0.28);
            padding: 2px 7px; border-radius: 6px;
        }
        .acc-uptime-pill.is-paused { color: #ffb84d; background: rgba(255, 159, 28, 0.10); border-color: rgba(255, 159, 28, 0.30); }

        .status-pill {
            display: inline-flex; align-items: center; gap: 6px;
            font-size: 12px; font-weight: 700;
            padding: 4px 12px; border-radius: 20px; letter-spacing: 0.3px;
        }
        .status-pill.online { background: rgba(0, 255, 200, 0.12); color: var(--accent-cyan); border: 1px solid rgba(0, 255, 200, 0.32); }
        .status-pill.in_match { background: rgba(168, 85, 247, 0.14); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.45); animation: pulse-border 1.5s infinite alternate; }
        .status-pill.searching { background: rgba(0, 179, 255, 0.13); color: #66d9ff; border: 1px solid rgba(0, 179, 255, 0.38); }
        .status-pill.paused { background: rgba(255, 159, 28, 0.12); color: #ffb84d; border: 1px solid rgba(255, 159, 28, 0.42); }
        .status-pill.offline, .status-pill.error { background: rgba(255, 46, 99, 0.12); color: var(--accent-red); border: 1px solid rgba(255, 46, 99, 0.32); }

        .status-pill.paused .radar-dot { background: #ffb84d; }
        .status-pill.paused .radar-dot::after { border-color: #ffb84d; animation: none; opacity: 0.35; }

        @keyframes pulse-border {
            0% { border-color: rgba(168, 85, 247, 0.35); }
            100% { border-color: rgba(168, 85, 247, 0.95); }
        }

        /* ==================== MODE SELECTOR ==================== */
        .mode-selector-row {
            display: flex; align-items: center; justify-content: space-between;
            background: rgba(0, 0, 0, 0.32);
            border: 1px solid rgba(168, 85, 247, 0.16);
            border-radius: var(--radius-md);
            padding: 8px 12px; margin-bottom: 12px; gap: 10px; flex-wrap: wrap;
        }

        .mode-selector-label {
            font-size: 11px; color: var(--text-muted);
            font-weight: 700; text-transform: uppercase; letter-spacing: 0.6px;
            display: flex; align-items: center; gap: 6px;
        }

        .mode-buttons { display: flex; gap: 6px; background: rgba(0, 0, 0, 0.4); padding: 3px; border-radius: 10px; border: 1px solid rgba(168, 85, 247, 0.18); }

        .mode-btn {
            border: 1px solid transparent; background: transparent;
            color: var(--text-dim); font-size: 11.5px; font-weight: 800;
            padding: 5px 12px; border-radius: 8px;
            cursor: pointer; font-family: 'Outfit', sans-serif;
            letter-spacing: 0.4px; display: inline-flex; align-items: center; gap: 5px;
        }

        .mode-btn:hover { color: var(--text-main); background: rgba(168, 85, 247, 0.10); }

        .mode-btn.active-lw {
            background: linear-gradient(135deg, rgba(168, 85, 247, 0.28), rgba(120, 80, 220, 0.18));
            color: #d8b4fe; border-color: rgba(168, 85, 247, 0.75);
            box-shadow: 0 0 12px rgba(168, 85, 247, 0.45);
        }
        .mode-btn.active-br {
            background: linear-gradient(135deg, rgba(255, 159, 28, 0.28), rgba(255, 130, 20, 0.18));
            color: #ffd27a; border-color: rgba(255, 159, 28, 0.75);
            box-shadow: 0 0 12px rgba(255, 159, 28, 0.45);
        }
        .mode-btn.active-auto {
            background: linear-gradient(135deg, rgba(0, 255, 200, 0.25), rgba(0, 179, 255, 0.18));
            color: #00ffc8; border-color: rgba(0, 255, 200, 0.75);
            box-shadow: 0 0 12px rgba(0, 255, 200, 0.45);
        }

        .auto-mode-hint {
            font-size: 10.5px; color: var(--text-dim); font-weight: 500;
            font-family: 'JetBrains Mono', monospace; margin-top: 3px;
            display: flex; align-items: center; gap: 5px;
        }

        /* ==================== EXP METRICS ==================== */
        .exp-strip {
            display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px;
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid rgba(168, 85, 247, 0.1);
            border-radius: var(--radius-md);
            padding: 12px 14px; margin-bottom: 14px;
        }

        .exp-col { display: flex; flex-direction: column; gap: 3px; }
        .exp-col span { font-size: 11px; color: var(--text-dim); text-transform: uppercase; font-weight: 600; }
        .exp-col strong { font-size: 14px; font-weight: 700; color: #fff; font-family: 'JetBrains Mono', monospace; }
        .exp-gained-highlight { color: #00ffc8 !important; display: flex; align-items: center; gap: 4px; }

        /* ==================== PROGRESS BAR ==================== */
        .card-progress {
            margin-bottom: 14px;
            background: rgba(255, 255, 255, 0.02);
            padding: 10px 12px; border-radius: 12px;
            border: 1px solid rgba(168, 85, 247, 0.1);
        }
        .progress-header {
            display: flex; justify-content: space-between;
            align-items: center; font-size: 11px;
            margin-bottom: 8px; font-weight: 600;
        }
        .prog-level-tag { color: var(--text-main); display: flex; align-items: center; gap: 6px; font-size: 11px; }
        .prog-needed-badge {
            background: rgba(168, 85, 247, 0.16); color: #c084fc;
            border: 1px solid rgba(168, 85, 247, 0.35);
            padding: 2px 8px; border-radius: 20px;
            font-size: 10px; font-weight: 700;
            display: flex; align-items: center; gap: 4px;
        }
        .progress-track {
            height: 9px; background: rgba(0, 0, 0, 0.55);
            border-radius: 12px; overflow: hidden;
            position: relative; border: 1px solid rgba(168, 85, 247, 0.15);
        }
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #00b3ff 0%, #00ffc8 25%, #a855f7 50%, #00ffc8 75%, #00b3ff 100%);
            background-size: 200% 100%;
            border-radius: 12px; transition: width 0.8s cubic-bezier(0.34, 1.56, 0.64, 1);
            animation: progressGlowShift 3s infinite linear;
            position: relative;
            box-shadow: 0 0 14px rgba(0, 255, 200, 0.55);
        }
        .progress-fill::before {
            content: ''; position: absolute; inset: 0;
            background: linear-gradient(90deg, transparent 0%, rgba(255, 255, 255, 0.7) 50%, transparent 100%);
            background-size: 200% 100%;
            animation: shimmerSweep 2.2s infinite linear;
        }
        @keyframes progressGlowShift { 0% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } 100% { background-position: 0% 50%; } }
        @keyframes shimmerSweep { 0% { transform: translateX(-150%); } 100% { transform: translateX(150%); } }

        .progress-sub { display: flex; justify-content: space-between; font-size: 10px; color: var(--text-dim); margin-top: 6px; }
        .prog-pct-pill {
            color: var(--accent-cyan); font-weight: 700; font-size: 10px;
            background: rgba(0, 255, 200, 0.10); padding: 1px 6px; border-radius: 8px;
            border: 1px solid rgba(0, 255, 200, 0.28);
        }

        /* ==================== ACCOUNT FOOTER ==================== */
        .acc-footer {
            display: flex; align-items: center; justify-content: space-between;
            padding-top: 10px; border-top: 1px solid rgba(168, 85, 247, 0.1);
            font-size: 12px; color: var(--text-dim); gap: 10px; flex-wrap: wrap;
        }
        .acc-btns { display: flex; align-items: center; gap: 8px; }

        .icon-btn {
            width: 34px; height: 34px; border-radius: 9px;
            background: rgba(168, 85, 247, 0.08);
            border: 1px solid rgba(168, 85, 247, 0.2);
            color: var(--text-muted);
            display: flex; align-items: center; justify-content: center;
            cursor: pointer;
        }
        .icon-btn:hover { background: rgba(0, 255, 200, 0.15); color: var(--accent-cyan); border-color: var(--accent-cyan); }
        .icon-btn.danger:hover { background: rgba(255, 46, 99, 0.15); color: var(--accent-red); border-color: var(--accent-red); }
        .icon-btn.btn-pause { color: #ffb84d; }
        .icon-btn.btn-pause:hover { background: rgba(255, 159, 28, 0.18); color: #ffb84d; border-color: #ffb84d; }
        .icon-btn.btn-resume { background: rgba(0, 255, 200, 0.14); color: #00ffc8; border-color: rgba(0, 255, 200, 0.4); }

        .btn-pause-all {
            display: inline-flex; align-items: center; gap: 6px;
            padding: 6px 12px; font-size: 12px; font-weight: 600;
            border-radius: 8px; background: rgba(255, 159, 28, 0.12);
            border: 1px solid rgba(255, 159, 28, 0.35);
            color: #ffb84d; cursor: pointer;
        }
        .btn-pause-all:hover { background: rgba(255, 159, 28, 0.22); border-color: #ffb84d; }
        .btn-pause-all.all-paused {
            background: rgba(0, 255, 200, 0.13);
            border-color: rgba(0, 255, 200, 0.4);
            color: #00ffc8;
        }

        /* ==================== CONSOLE ==================== */
        .console-panel { display: flex; flex-direction: column; height: 780px; }

        .console-tools {
            display: flex; align-items: center; justify-content: space-between;
            gap: 10px; margin-bottom: 12px; flex-wrap: wrap;
        }
        .console-filter-pills { display: flex; gap: 6px; }
        .console-pill {
            background: rgba(168, 85, 247, 0.06);
            border: 1px solid rgba(168, 85, 247, 0.2);
            color: var(--text-dim);
            padding: 4px 10px; border-radius: 6px;
            font-size: 11px; font-weight: 600; cursor: pointer;
        }
        .console-pill.active { background: rgba(0, 255, 200, 0.15); color: var(--accent-cyan); border-color: rgba(0, 255, 200, 0.35); }

        .toggle-btn {
            background: rgba(168, 85, 247, 0.06);
            border: 1px solid rgba(168, 85, 247, 0.2);
            color: var(--text-muted); padding: 4px 10px;
            border-radius: 6px; font-size: 11px; cursor: pointer;
            display: inline-flex; align-items: center; gap: 6px;
        }
        .toggle-btn.active { color: var(--accent-cyan); border-color: rgba(0, 255, 200, 0.38); }

        .console-stream {
            flex: 1; background: #06040e;
            border: 1px solid rgba(168, 85, 247, 0.12);
            border-radius: var(--radius-md);
            padding: 14px; font-family: 'JetBrains Mono', monospace;
            font-size: 12px; overflow-y: auto;
            display: flex; flex-direction: column; gap: 6px;
            position: relative;
        }
        .console-stream::-webkit-scrollbar { width: 6px; }
        .console-stream::-webkit-scrollbar-thumb { background: rgba(168, 85, 247, 0.3); border-radius: 3px; }

        .log-entry { line-height: 1.5; word-break: break-all; display: flex; align-items: flex-start; gap: 8px; animation: logSlideIn 0.25s ease-out; }
        @keyframes logSlideIn { 0% { opacity: 0; transform: translateX(-8px); } 100% { opacity: 1; transform: translateX(0); } }
        .log-ts { color: #4a4570; flex-shrink: 0; font-size: 11px; }
        .log-txt { flex: 1; }
        .log-txt.info { color: #66d9ff; }
        .log-txt.success { color: #00ffc8; }
        .log-txt.warning { color: #ffb84d; }
        .log-txt.error { color: #ff5c8a; }

        .btn-scroll-bottom {
            position: absolute; bottom: 12px; right: 14px;
            background: rgba(0, 255, 200, 0.18);
            border: 1px solid var(--accent-cyan); color: #fff;
            padding: 6px 12px; border-radius: 20px; font-size: 11px;
            cursor: pointer; display: none;
        }

        /* ==================== EMPTY ==================== */
        .empty-state { text-align: center; padding: 60px 20px; color: var(--text-dim); }
        .empty-state i { font-size: 48px; margin-bottom: 14px; color: rgba(168, 85, 247, 0.15); }
        .empty-state p { font-size: 14px; color: var(--text-muted); margin-bottom: 18px; }

        /* ==================== MODAL ==================== */
        .modal-backdrop {
            position: fixed; inset: 0;
            background: rgba(4, 2, 14, 0.88);
            backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
            display: none; align-items: center; justify-content: center;
            z-index: 100; padding: 20px;
        }
        .modal-backdrop.active { display: flex; }

        .modal-box {
            background: #0d0920; border: 1px solid var(--border-card);
            border-radius: var(--radius-lg); width: 100%; max-width: 480px;
            padding: 28px; box-shadow: 0 20px 60px rgba(0, 0, 0, 0.8);
            animation: modal-pop 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
        }
        @keyframes modal-pop { 0% { transform: scale(0.92) translateY(10px); opacity: 0; } 100% { transform: scale(1) translateY(0); opacity: 1; } }

        .modal-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 22px; }
        .modal-top h3 { font-size: 19px; font-weight: 700; display: flex; align-items: center; gap: 10px; }

        .form-field { margin-bottom: 18px; }
        .form-field label { display: block; font-size: 13px; font-weight: 600; color: var(--text-muted); margin-bottom: 6px; }

        .form-control {
            width: 100%; background: rgba(168, 85, 247, 0.06);
            border: 1px solid rgba(168, 85, 247, 0.25);
            border-radius: 12px; padding: 12px 16px;
            color: #fff; font-size: 14px; outline: none;
        }
        .form-control:focus { border-color: rgba(0, 255, 200, 0.55); background: rgba(0, 255, 200, 0.08); }

        /* ==================== TOAST ==================== */
        .toast-container {
            position: fixed; top: 24px; right: 24px;
            z-index: 1000; display: flex; flex-direction: column;
            gap: 10px; pointer-events: none;
        }
        .toast {
            background: rgba(13, 9, 32, 0.94);
            border: 1px solid var(--border-card);
            border-radius: var(--radius-md);
            padding: 12px 18px; color: #fff;
            font-size: 13px; font-weight: 600;
            backdrop-filter: blur(12px);
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
            display: flex; align-items: center; gap: 10px;
            pointer-events: auto;
            animation: toast-in 0.3s ease-out;
            max-width: 360px;
        }
        @keyframes toast-in { 0% { transform: translateX(50px); opacity: 0; } 100% { transform: translateX(0); opacity: 1; } }
        .toast.success { border-color: rgba(0, 255, 200, 0.55); color: #00ffc8; }
        .toast.error { border-color: rgba(255, 46, 99, 0.55); color: var(--accent-red); }
        .toast.info { border-color: rgba(0, 179, 255, 0.55); color: #66d9ff; }

        .mobile-header { display: none; }
        .mobile-bottom-nav { display: none; }

        /* ==================== RESPONSIVE ==================== */
        @media (max-width: 1024px) {
            .stats-hero { grid-template-columns: repeat(2, 1fr); }
            .workspace-grid { grid-template-columns: 1fr; }
            .console-panel { height: 500px; }
        }

        @media (max-width: 768px) {
            body { padding-bottom: 84px; }
            .app-shell { padding: 14px; }
            .desktop-header { display: none; }

            .mobile-header {
                display: flex; align-items: center; justify-content: space-between;
                padding: 12px 16px; background: var(--bg-card);
                border: 1px solid var(--border-card);
                border-radius: var(--radius-md);
                backdrop-filter: blur(16px);
                margin-bottom: 14px;
                position: sticky; top: 10px; z-index: 50;
            }
            .mobile-brand { display: flex; align-items: center; gap: 10px; }
            .mobile-brand h2 {
                font-size: 16px; font-weight: 800;
                background: linear-gradient(90deg, #fff, var(--accent-cyan));
                -webkit-background-clip: text; background-clip: text;
                -webkit-text-fill-color: transparent;
            }

            .stats-hero { grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 16px; }
            .stat-card { padding: 14px 16px; border-radius: var(--radius-md); }
            .stat-data h2 { font-size: 20px; }
            .stat-data p { font-size: 11px; }
            .stat-icon-wrap { width: 40px; height: 40px; font-size: 16px; }

            .mobile-tab-view { display: none; }
            .mobile-tab-view.active-tab { display: block; }
            .panel-box { padding: 16px; border-radius: var(--radius-md); }
            .console-panel { height: calc(100vh - 220px); }

            .modal-backdrop { align-items: flex-end; padding: 0; }
            .modal-box {
                max-width: 100%;
                border-radius: 24px 24px 0 0;
                padding: 24px 20px 36px 20px;
                animation: sheet-up 0.35s cubic-bezier(0.34, 1.56, 0.64, 1);
            }
            @keyframes sheet-up { 0% { transform: translateY(100%); } 100% { transform: translateY(0); } }

            .mobile-bottom-nav {
                position: fixed; bottom: 0; left: 0; right: 0;
                height: 68px; background: rgba(8, 6, 18, 0.96);
                border-top: 1px solid var(--border-card);
                backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
                display: flex; align-items: center; justify-content: space-around;
                z-index: 90; padding: 0 10px;
            }
            .mobile-nav-item {
                display: flex; flex-direction: column; align-items: center; justify-content: center;
                gap: 4px; color: var(--text-dim); font-size: 11px; font-weight: 600;
                text-decoration: none; background: none; border: none;
                cursor: pointer; padding: 8px 14px; border-radius: 12px;
            }
            .mobile-nav-item i { font-size: 18px; }
            .mobile-nav-item.active {
                color: var(--accent-cyan);
                background: rgba(0, 255, 200, 0.12);
            }
            .toast-container { top: 12px; left: 14px; right: 14px; }
        }
    </style>
</head>
<body>

    <div class="toast-container" id="toast-container"></div>

    <div class="app-shell">
        <!-- ==================== DESKTOP HEADER ==================== -->
        <header class="desktop-header">
            <div class="brand">
                <div class="brand-logo"><i class="fa-solid fa-bolt"></i></div>
                <div class="brand-title">
                    <h1>M4MUUU <span style="font-weight:400; color:var(--text-muted); font-size:16px;">| LEVEL UP BOT</span></h1>
                    <div class="brand-sub">
                        <span class="badge-live"><span class="radar-dot"></span> System Live</span>
                        <span>• LW / BR / AUTO Modes</span>
                        <span>• OB55 Parallel Engine</span>
                    </div>
                </div>
            </div>

            <div class="header-controls">
                <button class="btn btn-secondary" onclick="manualRefresh()"><i class="fa-solid fa-rotate"></i> Refresh</button>
                <button class="btn btn-primary" onclick="openAddModal()"><i class="fa-solid fa-plus"></i> Add Account</button>
            </div>
        </header>

        <!-- ==================== MOBILE HEADER ==================== -->
        <div class="mobile-header">
            <div class="mobile-brand">
                <div class="brand-logo" style="width:36px; height:36px; font-size:16px;">
                    <i class="fa-solid fa-bolt"></i>
                </div>
                <div>
                    <h2>M4MUUU</h2>
                    <span class="badge-live" style="font-size:9px; padding:1px 6px;"><span class="radar-dot" style="width:6px; height:6px;"></span> OB55 Live</span>
                </div>
            </div>
            <div style="display:flex; gap:8px;">
                <button class="icon-btn" onclick="manualRefresh()"><i class="fa-solid fa-rotate"></i></button>
                <button class="btn btn-primary" style="padding:7px 12px; font-size:12px;" onclick="openAddModal()"><i class="fa-solid fa-plus"></i> Add</button>
            </div>
        </div>

        <!-- ==================== MAIN WORKSPACE ==================== -->
        <div class="workspace-grid">
            <!-- LEFT: Accounts -->
            <div class="panel-box mobile-tab-view active-tab" id="tab-accounts">
                <div class="panel-header">
                    <div class="panel-title">
                        <i class="fa-solid fa-user-astronaut" style="color:var(--accent-cyan); font-size:20px;"></i>
                        <h3>Accounts & EXP Tracker</h3>
                        <span class="counter-pill" id="badge-acc-count">0 Loaded</span>
                    </div>
                    <div class="panel-actions">
                        <button class="btn-pause-all" id="btn-global-pause" onclick="togglePauseAll()">
                            <i class="fa-solid fa-pause"></i> <span>Pause All</span>
                        </button>
                    </div>
                </div>

                <div class="filter-row">
                    <div class="search-box">
                        <i class="fa-solid fa-magnifying-glass"></i>
                        <input type="text" id="account-search-input" placeholder="Search by Nickname or UID..." oninput="filterAccounts()">
                    </div>
                    <div class="filter-tabs">
                        <button class="filter-btn active" data-filter="all" onclick="setFilter('all', this)">All</button>
                        <button class="filter-btn" data-filter="in_match" onclick="setFilter('in_match', this)">In Match</button>
                        <button class="filter-btn" data-filter="online" onclick="setFilter('online', this)">Online</button>
                        <button class="filter-btn" data-filter="paused" onclick="setFilter('paused', this)">Paused</button>
                    </div>
                </div>

                <div class="accounts-grid" id="accounts-container">
                    <div class="empty-state">
                        <i class="fa-solid fa-circle-notch fa-spin"></i>
                        <p>Connecting to Bot Core...</p>
                    </div>
                </div>
            </div>

            <!-- RIGHT: Console -->
            <div class="panel-box console-panel mobile-tab-view" id="tab-console">
                <div class="panel-header">
                    <div class="panel-title">
                        <i class="fa-solid fa-terminal" style="color:var(--accent-green); font-size:18px;"></i>
                        <h3>Live Match Console</h3>
                    </div>
                    <div class="panel-actions">
                        <button class="icon-btn" onclick="clearLogs()"><i class="fa-solid fa-trash-can"></i></button>
                    </div>
                </div>

                <div class="console-tools">
                    <div class="console-filter-pills">
                        <button class="console-pill active" onclick="setLogFilter('all', this)">All</button>
                        <button class="console-pill" onclick="setLogFilter('match', this)">Matches</button>
                        <button class="console-pill" onclick="setLogFilter('success', this)">EXP Gained</button>
                        <button class="console-pill" onclick="setLogFilter('error', this)">Errors</button>
                    </div>
                    <div class="console-controls-right">
                        <button class="toggle-btn active" id="btn-autoscroll" onclick="toggleAutoScroll()">
                            <i class="fa-solid fa-arrows-down-to-line"></i> Auto-scroll
                        </button>
                    </div>
                </div>

                <div class="console-stream" id="console-stream" onscroll="handleLogScroll()"></div>

                <button class="btn-scroll-bottom" id="btn-jump-bottom" onclick="jumpToBottom()">
                    <i class="fa-solid fa-arrow-down"></i> Jump
                </button>
            </div>

            <!-- STATS TAB (mobile) -->
            <div class="panel-box mobile-tab-view" id="tab-stats" style="display:none;">
                <div class="panel-header">
                    <div class="panel-title">
                        <i class="fa-solid fa-chart-line" style="color:var(--accent-purple); font-size:20px;"></i>
                        <h3>Performance Analytics</h3>
                    </div>
                </div>

                <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:14px; margin-bottom:18px;">
                    <div class="stat-card cyan">
                        <div class="stat-data">
                            <p>Active Accounts</p>
                            <h2 id="stat-total-accounts">0</h2>
                            <div class="stat-badge cyan" id="stat-accounts-online-badge"><i class="fa-solid fa-circle-check"></i> 0 Online</div>
                        </div>
                        <div class="stat-icon-wrap cyan"><i class="fa-solid fa-users"></i></div>
                    </div>
                    <div class="stat-card green">
                        <div class="stat-data">
                            <p>Total EXP Gained</p>
                            <h2 id="stat-total-exp" style="color:var(--accent-green)">+0</h2>
                            <div class="stat-badge green" id="stat-exp-rate-badge"><i class="fa-solid fa-gauge-high"></i> ~0 EXP/hr</div>
                        </div>
                        <div class="stat-icon-wrap green"><i class="fa-solid fa-angles-up"></i></div>
                    </div>
                    <div class="stat-card purple">
                        <div class="stat-data">
                            <p>Matches Completed</p>
                            <h2 id="stat-total-matches">0</h2>
                            <div class="stat-badge purple" id="stat-active-matches-badge"><i class="fa-solid fa-gamepad"></i> 0 In Match</div>
                        </div>
                        <div class="stat-icon-wrap purple"><i class="fa-solid fa-trophy"></i></div>
                    </div>
                    <div class="stat-card amber">
                        <div class="stat-data">
                            <p>Bot Uptime</p>
                            <h2 id="stat-uptime">00:00:00</h2>
                            <div class="stat-badge" style="background:rgba(255,159,28,0.12); color:var(--accent-amber);"><i class="fa-solid fa-shield-halved"></i> Online</div>
                        </div>
                        <div class="stat-icon-wrap amber"><i class="fa-solid fa-clock"></i></div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- ==================== MOBILE NAV ==================== -->
    <nav class="mobile-bottom-nav">
        <button class="mobile-nav-item active" onclick="switchMobileTab('accounts', this)">
            <i class="fa-solid fa-user-group"></i><span>Accounts</span>
        </button>
        <button class="mobile-nav-item" onclick="switchMobileTab('stats', this)">
            <i class="fa-solid fa-chart-pie"></i><span>Stats</span>
        </button>
        <button class="mobile-nav-item" onclick="switchMobileTab('console', this)">
            <i class="fa-solid fa-terminal"></i><span>Console</span>
        </button>
        <button class="mobile-nav-item" onclick="openAddModal()">
            <i class="fa-solid fa-circle-plus" style="color:var(--accent-cyan)"></i><span>Add</span>
        </button>
    </nav>

    <!-- ==================== ADD MODAL ==================== -->
    <div class="modal-backdrop" id="add-modal">
        <div class="modal-box">
            <div class="modal-top">
                <h3><i class="fa-solid fa-user-plus" style="color:var(--accent-cyan)"></i> Add New Account</h3>
                <button class="icon-btn" onclick="closeAddModal()"><i class="fa-solid fa-xmark"></i></button>
            </div>
            <form id="add-acc-form" onsubmit="submitAddAccount(event)">
                <div class="form-field">
                    <label>Account Login Type</label>
                    <select class="form-control" id="acc-type" onchange="toggleFormType()">
                        <option value="guest">Guest Account (UID + Password)</option>
                        <option value="token">Access Token</option>
                    </select>
                </div>
                <div id="guest-inputs">
                    <div class="form-field">
                        <label>Guest UID</label>
                        <input type="text" class="form-control" id="acc-uid" placeholder="e.g. 7872038363" autocomplete="off">
                    </div>
                    <div class="form-field">
                        <label>Guest Password / Key</label>
                        <input type="text" class="form-control" id="acc-password" placeholder="e.g. 381002140664..." autocomplete="off">
                    </div>
                </div>
                <div id="token-inputs" style="display:none;">
                    <div class="form-field">
                        <label>Access Token</label>
                        <textarea class="form-control" id="acc-token" rows="3" placeholder="Paste access token here..."></textarea>
                    </div>
                </div>
                <div style="display:flex; justify-content:flex-end; gap:10px; margin-top:24px;">
                    <button type="button" class="btn btn-secondary" onclick="closeAddModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary"><i class="fa-solid fa-plus"></i> Save & Start</button>
                </div>
            </form>
        </div>
    </div>

    <script>
        const EXP_TABLE = {
            1: 0, 2: 48, 3: 202, 4: 544, 5: 1012, 6: 1844, 7: 2792, 8: 3800,
            9: 4870, 10: 6004, 11: 7192, 12: 8448, 13: 9760, 14: 11140, 15: 12566,
            16: 14060, 17: 15610, 18: 17224, 19: 18902, 20: 20632, 21: 22424, 22: 24278,
            23: 26192, 24: 28166, 25: 30200, 26: 32294, 27: 34448, 28: 37804, 29: 41274,
            30: 44870, 31: 48582, 32: 53394, 33: 58566, 34: 64096, 35: 69994, 36: 76460,
            37: 83506, 38: 91128, 39: 99322, 40: 108092, 41: 120144, 42: 133266, 43: 147472,
            44: 162760, 45: 179126, 46: 196572, 47: 215368, 48: 235516, 49: 257010, 50: 279860,
            51: 304056, 52: 348318, 53: 394982, 54: 444044, 55: 495508, 56: 549364, 57: 633756,
            58: 721744, 59: 813336, 60: 908522, 61: 1041438, 62: 1180352, 63: 1325266,
            64: 1476184, 65: 1634300, 66: 1840946, 67: 2056594, 68: 2281242, 69: 2514880,
            70: 2757530, 71: 3059506, 72: 3372284, 73: 3699456, 74: 4041030, 75: 4397002,
            76: 4829104, 77: 5282204, 78: 5756304, 79: 6251408, 80: 6776502, 81: 7381324,
            82: 8043154, 83: 8752982, 84: 9510808, 85: 10316338, 86: 11277190, 87: 12291748,
            88: 13360304, 89: 14482858, 90: 15659418, 91: 17026708, 92: 18453950, 93: 19941280,
            94: 21488570, 95: 23095858, 96: 24763138, 97: 26490428, 98: 28378704, 99: 30124996,
            100: 32032884
        };

        let cachedAccounts = [];
        let cachedLogs = [];
        let autoScrollEnabled = true;
        let activeFilter = 'all';
        let activeLogFilter = 'all';
        let localClientTime = Date.now();
        let modeActionInFlight = {}; // uid -> bool

        function showToast(message, type = 'info') {
            const container = document.getElementById('toast-container');
            const toast = document.createElement('div');
            toast.className = `toast ${type}`;
            const icon = type === 'success' ? 'fa-circle-check' : (type === 'error' ? 'fa-triangle-exclamation' : 'fa-circle-info');
            toast.innerHTML = `<i class="fa-solid ${icon}"></i> <span>${escapeHtml(message)}</span>`;
            container.appendChild(toast);
            setTimeout(() => {
                toast.style.opacity = '0';
                toast.style.transform = 'translateY(-10px)';
                toast.style.transition = 'all 0.3s ease';
                setTimeout(() => toast.remove(), 300);
            }, 3200);
        }

        function openAddModal() { document.getElementById('add-modal').classList.add('active'); }
        function closeAddModal() { document.getElementById('add-modal').classList.remove('active'); }

        function toggleFormType() {
            const type = document.getElementById('acc-type').value;
            document.getElementById('guest-inputs').style.display = type === 'guest' ? 'block' : 'none';
            document.getElementById('token-inputs').style.display = type === 'token' ? 'block' : 'none';
        }

        async function submitAddAccount(e) {
            e.preventDefault();
            const type = document.getElementById('acc-type').value;
            let payload = {};

            if (type === 'guest') {
                const uid = document.getElementById('acc-uid').value.trim();
                const password = document.getElementById('acc-password').value.trim();
                if (!uid || !password) return showToast('Please enter both UID and Password', 'error');
                payload = { uid, password };
            } else {
                const token = document.getElementById('acc-token').value.trim();
                if (!token) return showToast('Please paste a valid access token', 'error');
                payload = { token };
            }

            try {
                const res = await fetch('/api/account/add', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (data.status === 'ok') {
                    closeAddModal();
                    document.getElementById('add-acc-form').reset();
                    showToast('Account added and worker scheduled!', 'success');
                    fetchStats();
                } else {
                    showToast('Failed to add: ' + (data.error || 'Unknown error'), 'error');
                }
            } catch (err) {
                showToast('Network error adding account', 'error');
            }
        }

        async function deleteAccount(uid, authUid = '') {
            if (!confirm(`Remove account UID ${uid}?`)) return;
            try {
                const res = await fetch('/api/account/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ uid: uid, auth_uid: authUid })
                });
                const data = await res.json();
                if (data.status === 'ok') {
                    showToast(`Account ${uid} removed.`, 'info');
                    fetchStats();
                } else {
                    showToast('Error removing account: ' + (data.error || 'Unknown'), 'error');
                }
            } catch (err) {
                showToast('Network error during deletion', 'error');
            }
        }

        async function refreshAccountInfo(uid) {
            try {
                showToast(`Refreshing ${uid}...`, 'info');
                await fetch('/api/account/refresh', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ uid })
                });
                setTimeout(fetchStats, 1000);
            } catch (err) {
                showToast('Failed to refresh profile', 'error');
            }
        }

        async function restartAccount(uid) {
            try {
                showToast(`Restarting worker for ${uid}...`, 'info');
                await fetch('/api/account/restart', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ uid })
                });
                setTimeout(fetchStats, 1000);
            } catch (err) {
                showToast('Failed to restart worker', 'error');
            }
        }

        // ==================== MODE SETTER ====================
        async function setAccountMode(uid, mode) {
            if (modeActionInFlight[uid]) return;
            modeActionInFlight[uid] = true;

            try {
                const res = await fetch('/api/account/set_mode', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ uid, mode })
                });
                const data = await res.json();
                if (data.status === 'ok') {
                    let label = mode;
                    if (mode === 'LW') label = '🐺 Lone Wolf';
                    else if (mode === 'BR') label = '🎯 Battle Royale';
                    else label = '🔄 Auto (BR → LW at Lvl 3)';
                    showToast(`Mode set → ${label}`, 'success');
                    // Optimistically update cached account
                    const acc = cachedAccounts.find(a => String(a.uid) === String(uid));
                    if (acc) acc.mode_preference = mode;
                    renderAccounts(cachedAccounts);
                    setTimeout(fetchStats, 1200);
                } else {
                    showToast('Failed: ' + (data.error || 'Unknown'), 'error');
                }
            } catch (err) {
                showToast('Network error setting mode', 'error');
            } finally {
                setTimeout(() => { modeActionInFlight[uid] = false; }, 800);
            }
        }

        async function clearLogs() {
            try {
                await fetch('/api/logs/clear', { method: 'POST' });
                cachedLogs = [];
                document.getElementById('console-stream').innerHTML = '';
                showToast('Console logs cleared.', 'info');
            } catch (err) {
                showToast('Failed to clear logs', 'error');
            }
        }

        function copyUid(uid) {
            navigator.clipboard.writeText(uid).then(() => {
                showToast(`Copied UID ${uid}!`, 'success');
            }).catch(() => {
                showToast(`UID: ${uid}`, 'info');
            });
        }

        function manualRefresh() {
            fetchStats();
            showToast('Stats refreshed', 'info');
        }

        function setFilter(filter, btn) {
            activeFilter = filter;
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            if (btn) btn.classList.add('active');
            filterAccounts();
        }

        function formatDuration(sec) {
            sec = Math.max(0, Math.floor(sec || 0));
            const h = Math.floor(sec / 3600);
            const m = Math.floor((sec % 3600) / 60);
            const s = sec % 60;
            if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`;
            return `${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`;
        }

        async function togglePauseAccount(uid) {
            try {
                const res = await fetch('/api/account/pause', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ uid })
                });
                const data = await res.json();
                if (data.status === 'ok') {
                    showToast(data.is_paused ? `Paused ${uid}` : `Resumed ${uid}`, data.is_paused ? 'warning' : 'success');
                    fetchStats();
                } else {
                    showToast(data.error || 'Failed to toggle pause', 'error');
                }
            } catch (err) {
                showToast('Network error toggling pause', 'error');
            }
        }

        async function togglePauseAll() {
            try {
                const res = await fetch('/api/account/pause_all', { method: 'POST' });
                const data = await res.json();
                if (data.status === 'ok') {
                    showToast(data.all_paused ? 'All accounts PAUSED' : 'All accounts RESUMED', data.all_paused ? 'warning' : 'success');
                    fetchStats();
                } else {
                    showToast(data.error || 'Failed to toggle pause all', 'error');
                }
            } catch (err) {
                showToast('Network error toggling pause all', 'error');
            }
        }

        function filterAccounts() {
            const query = (document.getElementById('account-search-input').value || '').toLowerCase().trim();
            const cards = document.querySelectorAll('.acc-card');

            cards.forEach(card => {
                const uid = (card.dataset.uid || '').toLowerCase();
                const nick = (card.dataset.nick || '').toLowerCase();
                const status = (card.dataset.status || '').toLowerCase();
                const isPaused = card.dataset.isPaused === '1';

                const matchesQuery = !query || uid.includes(query) || nick.includes(query);
                let matchesFilter = true;

                if (activeFilter === 'in_match') {
                    matchesFilter = (status.includes('in_match') || status.includes('match')) && !isPaused;
                } else if (activeFilter === 'online') {
                    matchesFilter = (status.includes('online') || status.includes('searching')) && !isPaused;
                } else if (activeFilter === 'paused') {
                    matchesFilter = isPaused || status.includes('paused');
                }

                card.style.display = (matchesQuery && matchesFilter) ? 'block' : 'none';
            });
        }

        function switchMobileTab(tabName, btn) {
            document.querySelectorAll('.mobile-nav-item').forEach(b => b.classList.remove('active'));
            if (btn) btn.classList.add('active');

            const tabAccounts = document.getElementById('tab-accounts');
            const tabStats = document.getElementById('tab-stats');
            const tabConsole = document.getElementById('tab-console');

            if (tabName === 'accounts') {
                tabAccounts.style.display = 'block';
                tabStats.style.display = 'none';
                tabConsole.style.display = 'none';
            } else if (tabName === 'stats') {
                tabAccounts.style.display = 'none';
                tabStats.style.display = 'block';
                tabConsole.style.display = 'none';
            } else if (tabName === 'console') {
                tabAccounts.style.display = 'none';
                tabStats.style.display = 'none';
                tabConsole.style.display = 'flex';
                if (autoScrollEnabled) jumpToBottom();
            }
        }

        function setLogFilter(filter, btn) {
            activeLogFilter = filter;
            document.querySelectorAll('.console-pill').forEach(b => b.classList.remove('active'));
            if (btn) btn.classList.add('active');
            renderLogs(cachedLogs);
        }

        function toggleAutoScroll() {
            autoScrollEnabled = !autoScrollEnabled;
            const btn = document.getElementById('btn-autoscroll');
            if (autoScrollEnabled) {
                btn.classList.add('active');
                jumpToBottom();
            } else {
                btn.classList.remove('active');
            }
        }

        function handleLogScroll() {
            const stream = document.getElementById('console-stream');
            const isNearBottom = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 60;
            const jumpBtn = document.getElementById('btn-jump-bottom');
            jumpBtn.style.display = isNearBottom ? 'none' : 'block';
        }

        function jumpToBottom() {
            const stream = document.getElementById('console-stream');
            stream.scrollTop = stream.scrollHeight;
            document.getElementById('btn-jump-bottom').style.display = 'none';
        }

        // ==================== RENDER ACCOUNTS ====================
        function renderAccounts(accounts) {
            const container = document.getElementById('accounts-container');
            document.getElementById('badge-acc-count').innerText = `${accounts.length} Active`;

            const allPaused = accounts.length > 0 && accounts.every(a => a.is_paused || a.status === 'PAUSED');
            const globalPauseBtn = document.getElementById('btn-global-pause');
            if (globalPauseBtn) {
                if (allPaused) {
                    globalPauseBtn.className = 'btn-pause-all all-paused';
                    globalPauseBtn.innerHTML = '<i class="fa-solid fa-play"></i> <span>Resume All</span>';
                } else {
                    globalPauseBtn.className = 'btn-pause-all';
                    globalPauseBtn.innerHTML = '<i class="fa-solid fa-pause"></i> <span>Pause All</span>';
                }
            }

            if (accounts.length === 0) {
                container.innerHTML = `
                    <div class="empty-state">
                        <i class="fa-solid fa-ghost"></i>
                        <p>No active accounts. Add one to start farming!</p>
                        <button class="btn btn-primary" onclick="openAddModal()"><i class="fa-solid fa-plus"></i> Add First Account</button>
                    </div>`;
                return;
            }

            if (container.querySelector('.empty-state')) container.innerHTML = '';

            const currentUids = new Set(accounts.map(a => String(a.uid)));
            container.querySelectorAll('.acc-card').forEach(card => {
                if (!currentUids.has(card.dataset.uid)) card.remove();
            });

            accounts.forEach(acc => {
                const uid = String(acc.uid);
                const gainedExp = acc.gained_exp || 0;
                const isPaused = Boolean(acc.is_paused || acc.status === 'PAUSED');
                const uptimeSec = Math.floor(acc.uptime_seconds || 0);
                const status = isPaused ? 'PAUSED' : (acc.status || 'ONLINE');
                const statusClass = isPaused ? 'paused' : (status === 'IN_MATCH' ? 'in_match' : (status === 'SEARCHING' ? 'searching' : (status === 'ONLINE' ? 'online' : 'error')));
                const statusText = isPaused ? 'PAUSED' : (status === 'IN_MATCH' ? `In Match (${acc.active_matches || 1})` : status);

                const lvl = Math.max(1, parseInt(acc.level) || 1);
                const nextLvl = Math.min(100, lvl + 1);
                const baseExp = EXP_TABLE[lvl] !== undefined ? EXP_TABLE[lvl] : 0;
                const targetExp = EXP_TABLE[nextLvl] !== undefined ? EXP_TABLE[nextLvl] : (baseExp + 50000);
                const neededForLevel = Math.max(1, targetExp - baseExp);
                const earnedInLevel = Math.max(0, acc.current_exp - baseExp);
                const remainingExp = Math.max(0, targetExp - acc.current_exp);
                const progressPct = lvl >= 100 ? 100 : Math.min(100, Math.max(0, (earnedInLevel / neededForLevel) * 100));

                // Mode preference
                const modePref = (acc.mode_preference || 'AUTO').toUpperCase();
                const autoHint = modePref === 'AUTO'
                    ? (lvl < 3
                        ? '<div class="auto-mode-hint"><i class="fa-solid fa-crosshairs"></i> Currently BR • Auto switches to LW at Lv3</div>'
                        : '<div class="auto-mode-hint"><i class="fa-solid fa-shield-halved"></i> Currently LW (Lv3+)</div>')
                    : '';

                let card = document.getElementById(`acc-card-${uid}`);
                if (!card) {
                    card = document.createElement('div');
                    card.id = `acc-card-${uid}`;
                    card.className = 'acc-card';
                    card.dataset.uid = uid;
                    container.appendChild(card);
                }

                card.dataset.nick = acc.nickname || '';
                card.dataset.status = statusClass;
                card.dataset.isPaused = isPaused ? '1' : '0';

                card.innerHTML = `
                    <div class="acc-header">
                        <div class="acc-identity">
                            <div class="acc-avatar">
                                <i class="fa-solid fa-gamepad"></i>
                                <span class="level-badge">L${acc.level || 1}</span>
                            </div>
                            <div class="acc-details">
                                <h4>${escapeHtml(acc.nickname)} <span class="region-tag">${acc.region || 'BD'}</span></h4>
                                <div class="acc-uid-row">
                                    <span class="acc-uid">UID: ${uid}</span>
                                    <button class="btn-copy" onclick="copyUid('${uid}')"><i class="fa-regular fa-copy"></i></button>
                                    <span class="acc-uptime-pill ${isPaused ? 'is-paused' : ''}">
                                        <i class="fa-solid fa-stopwatch"></i>
                                        <span class="acc-uptime-val" data-uid="${uid}" data-uptime="${uptimeSec}" data-paused="${isPaused ? '1' : '0'}">${formatDuration(uptimeSec)}</span>
                                    </span>
                                </div>
                            </div>
                        </div>
                        <div class="status-pill ${statusClass}">
                            <span class="radar-dot"></span>
                            <span>${statusText}</span>
                        </div>
                    </div>

                    <!-- ============ MODE SELECTOR ============ -->
                    <div class="mode-selector-row">
                        <div class="mode-selector-label">
                            <i class="fa-solid fa-sliders" style="color:var(--accent-cyan);"></i> Match Mode
                        </div>
                        <div class="mode-buttons">
                            <button class="mode-btn ${modePref === 'LW' ? 'active-lw' : ''}"
                                    onclick="setAccountMode('${uid}', 'LW')"
                                    title="Lone Wolf (mode_id 43)">
                                <i class="fa-solid fa-shield-halved"></i> LW
                            </button>
                            <button class="mode-btn ${modePref === 'BR' ? 'active-br' : ''}"
                                    onclick="setAccountMode('${uid}', 'BR')"
                                    title="Battle Royale (mode_id 1)">
                                <i class="fa-solid fa-crosshairs"></i> BR
                            </button>
                            <button class="mode-btn ${modePref === 'AUTO' ? 'active-auto' : ''}"
                                    onclick="setAccountMode('${uid}', 'AUTO')"
                                    title="Auto — BR till Level 3, then Lone Wolf">
                                <i class="fa-solid fa-wand-magic-sparkles"></i> Auto
                            </button>
                        </div>
                    </div>
                    ${autoHint}

                    <div class="exp-strip">
                        <div class="exp-col">
                            <span>Initial EXP</span>
                            <strong>${(acc.initial_exp || 0).toLocaleString()}</strong>
                        </div>
                        <div class="exp-col">
                            <span>Current EXP</span>
                            <strong>${(acc.current_exp || 0).toLocaleString()}</strong>
                        </div>
                        <div class="exp-col">
                            <span>Session Gain</span>
                            <strong class="exp-gained-highlight">+${gainedExp.toLocaleString()}</strong>
                        </div>
                    </div>

                    <div class="card-progress">
                        <div class="progress-header">
                            <span class="prog-level-tag">
                                <i class="fa-solid fa-angles-up" style="color:var(--accent-green);"></i>
                                Level ${lvl} → <strong style="color:var(--accent-cyan); font-weight:700;">L${nextLvl}</strong>
                            </span>
                            <span class="prog-needed-badge">
                                <i class="fa-solid fa-bolt"></i> ${remainingExp.toLocaleString()} EXP needed
                            </span>
                        </div>
                        <div class="progress-track">
                            <div class="progress-fill" style="width: ${progressPct.toFixed(1)}%"></div>
                        </div>
                        <div class="progress-sub">
                            <span>${earnedInLevel.toLocaleString()} / ${neededForLevel.toLocaleString()} EXP</span>
                            <span class="prog-pct-pill">${progressPct.toFixed(1)}%</span>
                        </div>
                    </div>

                    <div class="acc-footer">
                        <span>Last match: ${acc.last_match_time || 'Running...'}</span>
                        <div class="acc-btns">
                            <button class="icon-btn ${isPaused ? 'btn-resume' : 'btn-pause'}" onclick="togglePauseAccount('${uid}')" title="${isPaused ? 'Resume' : 'Pause'}">
                                <i class="fa-solid ${isPaused ? 'fa-play' : 'fa-pause'}"></i>
                            </button>
                            <button class="icon-btn" onclick="restartAccount('${uid}')" title="Restart Worker"><i class="fa-solid fa-arrow-rotate-right"></i></button>
                            <button class="icon-btn" onclick="refreshAccountInfo('${uid}')" title="Refresh Profile EXP"><i class="fa-solid fa-arrows-rotate"></i></button>
                            <button class="icon-btn danger" onclick="deleteAccount('${uid}', '${acc.auth_uid || ''}')" title="Delete"><i class="fa-solid fa-trash"></i></button>
                        </div>
                    </div>
                `;
            });

            filterAccounts();
        }

        function renderLogs(logs) {
            const stream = document.getElementById('console-stream');
            let filtered = logs;

            if (activeLogFilter === 'match') {
                filtered = logs.filter(l => l.message.includes('Match') || l.message.includes('⚔'));
            } else if (activeLogFilter === 'success') {
                filtered = logs.filter(l => l.level === 'success' || l.message.includes('EXP'));
            } else if (activeLogFilter === 'error') {
                filtered = logs.filter(l => l.level === 'error' || l.level === 'warning');
            }

            stream.innerHTML = filtered.map(log => {
                const lvl = log.level || 'info';
                return `
                    <div class="log-entry">
                        <span class="log-ts">[${log.time || '00:00:00'}]</span>
                        <span class="log-txt ${lvl}">${escapeHtml(log.message)}</span>
                    </div>`;
            }).join('');

            if (autoScrollEnabled) jumpToBottom();
        }

        function setSafeText(id, val) {
            const el = document.getElementById(id);
            if (el) el.innerText = (val !== undefined && val !== null) ? val : '';
        }

        function setSafeHtml(id, val) {
            const el = document.getElementById(id);
            if (el) el.innerHTML = (val !== undefined && val !== null) ? val : '';
        }

        async function fetchStats() {
            try {
                const res = await fetch('/api/stats');
                const data = await res.json();

                setSafeText('stat-total-accounts', data.total_accounts);
                setSafeText('stat-total-exp', '+' + data.total_gained_exp.toLocaleString());
                setSafeText('stat-total-matches', data.total_matches);

                const onlineCount = data.accounts.filter(a => a.status === 'ONLINE' || a.status === 'IN_MATCH').length;
                setSafeHtml('stat-accounts-online-badge', `<i class="fa-solid fa-circle-check"></i> ${onlineCount} Online`);
                setSafeHtml('stat-exp-rate-badge', `<i class="fa-solid fa-gauge-high"></i> ~${(data.exp_per_hour || 0).toLocaleString()} EXP/hr`);
                setSafeHtml('stat-active-matches-badge', `<i class="fa-solid fa-gamepad"></i> ${data.total_active_matches || 0} In Match`);

                cachedAccounts = data.accounts;
                renderAccounts(data.accounts);

                cachedLogs = data.logs;
                renderLogs(data.logs);
            } catch (err) {
                console.warn("Stats poll error:", err);
            }
        }

        function escapeHtml(text) {
            if (!text) return '';
            return String(text)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;');
        }

        setInterval(() => {
            const sec = Math.floor((Date.now() - localClientTime) / 1000);
            const h = String(Math.floor(sec / 3600)).padStart(2, '0');
            const m = String(Math.floor((sec % 3600) / 60)).padStart(2, '0');
            const s = String(sec % 60).padStart(2, '0');
            setSafeText('stat-uptime', `${h}:${m}:${s}`);

            document.querySelectorAll('.acc-uptime-val').forEach(el => {
                if (el.dataset.paused !== '1') {
                    let cur = parseInt(el.dataset.uptime || '0', 10) + 1;
                    el.dataset.uptime = cur;
                    el.innerText = formatDuration(cur);
                }
            });
        }, 1000);

        fetchStats();
        setInterval(fetchStats, 2000);
    </script>
</body>
</html>
"""


# ============================================================
#  HTTP HANDLERS
# ============================================================

async def handle_index(request: web.Request) -> web.Response:
    return web.Response(text=INDEX_HTML, content_type="text/html", charset="utf-8")


async def handle_get_stats(request: web.Request) -> web.Response:
    accounts_data = list(bot_state.accounts.values())
    accounts_data.sort(key=lambda x: x.get("gained_exp", 0), reverse=True)
    uptime_sec = max(1, int(time.time() - bot_state.start_time))
    total_gained = bot_state.total_gained_exp
    exp_per_hour = int((total_gained / uptime_sec) * 3600)
    total_active_matches = sum(acc.get("active_matches", 0) for acc in accounts_data)

    for acc in accounts_data:
        uid_k = str(acc.get("uid", ""))
        acc["uptime_seconds"] = bot_state.get_account_uptime(uid_k)
        acc["is_paused"] = bot_state.is_paused(uid_k)
        if "mode_preference" not in acc:
            acc["mode_preference"] = bot_state.get_account_mode_preference(uid_k)

    return web.json_response({
        "total_accounts": len(bot_state.accounts),
        "total_matches": bot_state.total_matches,
        "total_active_matches": total_active_matches,
        "total_gained_exp": total_gained,
        "exp_per_hour": exp_per_hour,
        "accounts": accounts_data,
        "logs": bot_state.logs[-80:],
        "uptime": uptime_sec
    })


async def handle_add_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        accounts_file = "accounts.json"
        existing = []
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
                return web.json_response({"status": "error", "error": "UID and Password required"})

            if uid in bot_state.account_workers:
                try:
                    bot_state.account_workers[uid].cancel()
                except Exception:
                    pass
                bot_state.account_workers.pop(uid, None)

            existing = [acc for acc in existing if str(acc.get("uid", "")) != uid]
            existing.append({"uid": uid, "password": pwd})
            identifier = uid
        elif "token" in data:
            token = str(data["token"]).strip()
            if not token:
                return web.json_response({"status": "error", "error": "Token required"})

            tok_key = token[:16]
            for k in list(bot_state.account_workers.keys()):
                if k == tok_key or k.startswith(tok_key[:10]) or tok_key.startswith(k[:10]):
                    try:
                        bot_state.account_workers[k].cancel()
                    except Exception:
                        pass
                    bot_state.account_workers.pop(k, None)

            existing = [acc for acc in existing if acc.get("token", "") != token]
            existing.append({"token": token})
            identifier = f"Token_{token[:8]}..."
        else:
            return web.json_response({"status": "error", "error": "Invalid payload"})

        with open(accounts_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)

        bot_state.log(f"New account added: {identifier}", "success")
        if "on_account_added" in bot_state.refresh_callbacks:
            asyncio.create_task(bot_state.refresh_callbacks["on_account_added"](data))
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_delete_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        req_uid = str(data.get("uid", "")).strip()
        req_auth_uid = str(data.get("auth_uid", "")).strip()
        if not req_uid and not req_auth_uid:
            return web.json_response({"status": "error", "error": "UID required"})

        candidate_ids = set()
        if req_uid: candidate_ids.add(req_uid)
        if req_auth_uid: candidate_ids.add(req_auth_uid)

        for cid in list(candidate_ids):
            if cid in bot_state.game_to_auth_id:
                candidate_ids.add(str(bot_state.game_to_auth_id[cid]))
            if cid in bot_state.auth_to_game_id:
                candidate_ids.add(str(bot_state.auth_to_game_id[cid]))

        target_tokens = set()
        for cid in list(candidate_ids):
            acc_info = bot_state.accounts.get(cid, {})
            if acc_info:
                if acc_info.get("auth_uid"): candidate_ids.add(str(acc_info["auth_uid"]))
                if acc_info.get("uid"): candidate_ids.add(str(acc_info["uid"]))
                t = acc_info.get("token") or acc_info.get("access_token")
                if t: target_tokens.add(str(t))

        for cid in list(candidate_ids):
            creds = bot_state.account_credentials.get(cid, {})
            if creds:
                if creds.get("auth_uid"): candidate_ids.add(str(creds["auth_uid"]))
                if creds.get("account_id"): candidate_ids.add(str(creds["account_id"]))
                t = creds.get("token") or creds.get("access_token") or creds.get("auth_token")
                if t: target_tokens.add(str(t))

        token_cache_file = "token_cache.json"
        if os.path.exists(token_cache_file):
            try:
                with open(token_cache_file, "r", encoding="utf-8") as f:
                    tcache = json.load(f)
                dirty = False
                for k, v in list(tcache.items()):
                    k_str = str(k)
                    v_acc_id = str(v.get("account_id", ""))
                    v_auth_uid = str(v.get("auth_uid", ""))
                    if k_str in candidate_ids or v_acc_id in candidate_ids or v_auth_uid in candidate_ids:
                        candidate_ids.add(k_str)
                        if v_acc_id: candidate_ids.add(v_acc_id)
                        if v_auth_uid: candidate_ids.add(v_auth_uid)
                        del tcache[k]
                        dirty = True
                if dirty:
                    with open(token_cache_file, "w", encoding="utf-8") as f:
                        json.dump(tcache, f, indent=2)
            except Exception:
                pass

        accounts_file = "accounts.json"
        if os.path.exists(accounts_file):
            try:
                with open(accounts_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                new_existing = []
                for acc in existing:
                    acc_uid = str(acc.get("uid", "")).strip()
                    acc_tok = str(acc.get("token", "")).strip()
                    is_match = False
                    if acc_uid and acc_uid in candidate_ids: is_match = True
                    if acc_tok and (acc_tok in candidate_ids or acc_tok in target_tokens): is_match = True
                    for tok in target_tokens:
                        if acc_tok and (acc_tok.startswith(tok[:16]) or tok.startswith(acc_tok[:16])):
                            is_match = True
                    if not is_match:
                        new_existing.append(acc)
                with open(accounts_file, "w", encoding="utf-8") as f:
                    json.dump(new_existing, f, indent=2)
            except Exception:
                pass

        devices_file = "devices.json"
        if os.path.exists(devices_file):
            try:
                with open(devices_file, "r", encoding="utf-8") as f:
                    devices_data = json.load(f)
                dirty = False
                for dev_k in list(devices_data.keys()):
                    dev_k_str = str(dev_k)
                    if dev_k_str in candidate_ids:
                        del devices_data[dev_k]
                        dirty = True
                    else:
                        for tok in target_tokens:
                            if dev_k_str == tok[:16] or tok.startswith(dev_k_str):
                                del devices_data[dev_k]
                                dirty = True
                                break
                if dirty:
                    with open(devices_file, "w", encoding="utf-8") as f:
                        json.dump(devices_data, f, indent=4)
            except Exception:
                pass

        for cid in candidate_ids:
            bot_state.accounts.pop(cid, None)
            bot_state.account_credentials.pop(cid, None)
            bot_state.auth_to_game_id.pop(cid, None)
            bot_state.game_to_auth_id.pop(cid, None)
            bot_state.account_token_map.pop(cid, None)
            bot_state.account_mode_preferences.pop(cid, None)

        cancelled_keys = []
        for k, worker in list(bot_state.account_workers.items()):
            k_str = str(k)
            should_cancel = False
            if k_str in candidate_ids: should_cancel = True
            for tok in target_tokens:
                if k_str == tok[:16] or tok.startswith(k_str[:10]): should_cancel = True
            if should_cancel:
                try: worker.cancel()
                except Exception: pass
                cancelled_keys.append(k)

        for k in cancelled_keys:
            bot_state.account_workers.pop(k, None)
        for cid in candidate_ids:
            bot_state.close_writers_for_account(cid)

        if "on_account_deleted" in bot_state.refresh_callbacks:
            try:
                asyncio.create_task(bot_state.refresh_callbacks["on_account_deleted"](list(candidate_ids)))
            except Exception:
                pass

        target_repr = req_uid or req_auth_uid
        bot_state.log(f"Account {target_repr} deleted.", "warning", target_repr)
        bot_state.recalc_totals()
        return web.json_response({"status": "ok", "deleted": list(candidate_ids)})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_refresh_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        if "on_refresh_account" in bot_state.refresh_callbacks:
            asyncio.create_task(bot_state.refresh_callbacks["on_refresh_account"](uid))
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_restart_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        if "on_restart_account" in bot_state.refresh_callbacks:
            asyncio.create_task(bot_state.refresh_callbacks["on_restart_account"](uid))
        elif "on_refresh_account" in bot_state.refresh_callbacks:
            asyncio.create_task(bot_state.refresh_callbacks["on_refresh_account"](uid))
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_clear_logs(request: web.Request) -> web.Response:
    bot_state.logs.clear()
    return web.json_response({"status": "ok"})


async def handle_toggle_pause(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        if not uid:
            return web.json_response({"status": "error", "error": "UID required"})
        is_paused = bot_state.toggle_pause(uid)
        return web.json_response({"status": "ok", "is_paused": is_paused})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_toggle_pause_all(request: web.Request) -> web.Response:
    try:
        paused_state = bot_state.toggle_pause_all()
        return web.json_response({"status": "ok", "all_paused": paused_state})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


# ==================== MODE SETTER HANDLER ====================
async def handle_set_mode(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        mode = str(data.get("mode", "AUTO")).strip().upper()

        if not uid:
            return web.json_response({"status": "error", "error": "UID required"})
        if mode not in ("LW", "BR", "AUTO"):
            return web.json_response({"status": "error", "error": "mode must be LW / BR / AUTO"})

        # Update preference immediately
        bot_state.set_account_mode_preference(uid, mode)
        bot_state.log(f"🎯 UID {uid} mode preference → {mode}", "success", uid)

        # Notify main.py to restart the worker with new mode
        if "on_set_mode" in bot_state.refresh_callbacks:
            asyncio.create_task(bot_state.refresh_callbacks["on_set_mode"](uid, mode))

        return web.json_response({"status": "ok", "mode": mode})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


# ============================================================
#  START WEB DASHBOARD
# ============================================================

async def start_web_dashboard(host: str = "0.0.0.0", port: int = 8080):
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/stats", handle_get_stats)
    app.router.add_post("/api/account/add", handle_add_account)
    app.router.add_post("/api/account/delete", handle_delete_account)
    app.router.add_post("/api/account/refresh", handle_refresh_account)
    app.router.add_post("/api/account/restart", handle_restart_account)
    app.router.add_post("/api/account/pause", handle_toggle_pause)
    app.router.add_post("/api/account/pause_all", handle_toggle_pause_all)
    app.router.add_post("/api/account/set_mode", handle_set_mode)
    app.router.add_post("/api/logs/clear", handle_clear_logs)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner