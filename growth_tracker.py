#!/usr/bin/env python3
"""
GrowthTracker - Compound Growth Tracking for Phase 1
Replaces GoalTracker until ₹5000 target reached
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


class GrowthTracker:
    """Track compound growth progress and stats"""
    
    PHASE1_TARGET = 5000
    PHASE2_TARGET = 50000
    
    def __init__(self, state_file=None):
        self.state_file = state_file or (Path(__file__).parent / "growth_state.json")
        self.starting_capital = 500
        self.current_capital = 500
        self.total_profit = 0
        self.best_trade = 0
        self.worst_trade = 0
        self.total_trades = 0
        self.winning_trades = 0
        self.daily_stats = []
        self.milestones_hit = []
        self.phase = 1
        self.trade_history = []
        
        self.load_state()
    
    def load_state(self):
        if not self.state_file.exists():
            return
        try:
            with open(self.state_file) as f:
                state = json.load(f)
            self.starting_capital = state.get('starting_capital', 500)
            self.current_capital = state.get('current_capital', 500)
            self.total_profit = state.get('total_profit', 0)
            self.best_trade = state.get('best_trade', 0)
            self.worst_trade = state.get('worst_trade', 0)
            self.total_trades = state.get('total_trades', 0)
            self.winning_trades = state.get('winning_trades', 0)
            self.daily_stats = state.get('daily_stats', [])
            self.milestones_hit = state.get('milestones_hit', [])
            self.phase = state.get('phase', 1)
            self.trade_history = state.get('trade_history', [])
        except Exception as e:
            print(f"Load error: {e}")
    
    def save_state(self):
        try:
            state = {
                'starting_capital': self.starting_capital,
                'current_capital': self.current_capital,
                'total_profit': self.total_profit,
                'best_trade': self.best_trade,
                'worst_trade': self.worst_trade,
                'total_trades': self.total_trades,
                'winning_trades': self.winning_trades,
                'daily_stats': self.daily_stats[-7:],
                'milestones_hit': self.milestones_hit,
                'phase': self.phase,
                'trade_history': self.trade_history[-100:],
                'saved_at': datetime.now().isoformat()
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"Save error: {e}")
    
    def record_trade(self, pnl: float, symbol: str):
        """Record a completed trade"""
        self.total_trades += 1
        self.total_profit += pnl
        self.current_capital += pnl
        
        if pnl > 0:
            self.winning_trades += 1
            self.best_trade = max(self.best_trade, pnl)
        else:
            self.worst_trade = min(self.worst_trade, pnl)
        
        self.trade_history.append({
            'symbol': symbol,
            'pnl': pnl,
            'time': datetime.now().isoformat()
        })
        
        self.check_milestones()
        self.save_state()
    
    def check_milestones(self):
        """Check and celebrate milestones"""
        milestones = [
            (750, "25% growth!"),
            (1000, "2x achieved!"),
            (2000, "4x! Halfway to Phase 2!"),
            (5000, "PHASE 2 UNLOCKED! Savings mode ON!")
        ]
        
        for target, message in milestones:
            if self.current_capital >= target and target not in self.milestones_hit:
                self.milestones_hit.append(target)
                print(f"\n{message}\n")
                return message
        
        return None
    
    def get_win_rate(self) -> float:
        if self.total_trades == 0:
            return 0
        return (self.winning_trades / self.total_trades) * 100
    
    def get_projected_days(self) -> Optional[int]:
        """Project days to reach 5000 based on last 7 days"""
        if len(self.daily_stats) < 3:
            return None
        
        recent = self.daily_stats[-7:]
        avg_daily = sum(recent) / len(recent) if recent else 0
        
        if avg_daily <= 0:
            return None
        
        remaining = self.PHASE1_TARGET - self.current_capital
        if remaining <= 0:
            return 0
        
        return int(remaining / avg_daily)
    
    def get_progress_bar(self, width: int = 20) -> str:
        progress = min(self.current_capital / self.PHASE1_TARGET, 1.0)
        filled = int(progress * width)
        bar = "#" * filled + "-" * (width - filled)
        return f"[{bar}]"
    
    def add_daily_pnl(self, pnl: float):
        """Add today's P&L to daily stats"""
        self.daily_stats.append(pnl)
        self.daily_stats = self.daily_stats[-7:]
        self.save_state()
    
    def get_position_size(self, capital: float, open_positions: int) -> float:
        """Calculate position size"""
        if open_positions == 0:
            size = capital * 0.20
        else:
            size = min(capital * 0.20, capital / open_positions)
        
        if capital < 1000:
            return min(size, 100)
        elif capital < 2000:
            return min(size, 200)
        elif capital < 5000:
            return min(size, 500)
        else:
            return size
    
    def is_phase2(self) -> bool:
        return self.current_capital >= self.PHASE1_TARGET
    
    def reset_daily(self):
        """Reset for new day"""
        pass


if __name__ == "__main__":
    tracker = GrowthTracker()
    tracker.current_capital = 750
    tracker.check_milestones()
    print(f"Progress: {tracker.get_progress_bar()}")
    print(f"Position size @ 500 w/ 3 pos: {tracker.get_position_size(500, 3)}")