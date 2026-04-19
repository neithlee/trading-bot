#!/usr/bin/env python3
"""
GoalTracker - Savings Goal Tracking Module for Trading Bot
Tracks progress toward financial goals and adjusts trading aggression
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

# Default savings goals (₹)
DEFAULT_GOALS = {
    "openclaw_hardware": {
        "target": 15000,
        "description": "CPU/RAM upgrade for OpenClaw AI agent framework",
        "priority": 1,
        "one_time": True
    },
    "ai_model_api": {
        "target": 5000,
        "description": "Monthly AI model API subscription (Claude/GPT-4/Gemini)",
        "priority": 2,
        "one_time": False,
        "recurring": True
    },
    "zerodha_api": {
        "target": 2000,
        "description": "Zerodha Kite Connect API (₹2000/month)",
        "priority": 3,
        "one_time": False,
        "recurring": True
    },
    "vps_hosting": {
        "target": 1000,
        "description": "Monthly VPS/cloud storage for 24/7 bot operation",
        "priority": 4,
        "one_time": False,
        "recurring": True
    },
    "capital_buffer": {
        "target": 5000,
        "description": "Emergency trading capital buffer",
        "priority": 5,
        "one_time": True
    }
}


class GoalTracker:
    """Track savings progress toward financial goals"""
    
    def __init__(self, state_file: str = None):
        self.state_file = state_file or (Path(__file__).parent / "goal_state.json")
        self.goals = DEFAULT_GOALS.copy()
        self.savings_accumulated = {goal: 0.0 for goal in self.goals}
        self.total_profits_tracked = 0.0
        self.profit_transfer_percent = 30  # 30% of profits go to savings
        self.achieved_goals = []
        self.daily_stats = {
            "trades": 0,
            "pnl": 0.0,
            "savings_added": 0.0
        }
        
        # Load saved state
        self.load_state()
    
    def load_state(self):
        """Load goal tracker state from file"""
        if not self.state_file.exists():
            return
        
        try:
            with open(self.state_file) as f:
                state = json.load(f)
            
            self.savings_accumulated = state.get('savings_accumulated', {goal: 0.0 for goal in self.goals})
            self.total_profits_tracked = state.get('total_profits_tracked', 0.0)
            self.achieved_goals = state.get('achieved_goals', [])
            self.daily_stats = state.get('daily_stats', {"trades": 0, "pnl": 0.0, "savings_added": 0.0})
            
            # Load custom goals if present
            if 'goals' in state:
                self.goals.update(state['goals'])
                
        except Exception as e:
            print(f"Failed to load goal state: {e}")
    
    def save_state(self):
        """Save goal tracker state to file"""
        try:
            state = {
                'savings_accumulated': self.savings_accumulated,
                'total_profits_tracked': self.total_profits_tracked,
                'achieved_goals': self.achieved_goals,
                'daily_stats': self.daily_stats,
                'goals': self.goals,
                'saved_at': datetime.now().isoformat()
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"Failed to save goal state: {e}")
    
    def add_profit(self, pnl: float, trade_count: int = 1) -> Dict[str, float]:
        """
        Add profit and transfer to savings
        Returns: dict of savings added per goal
        """
        if pnl <= 0:
            return {}
        
        # Calculate savings transfer
        savings_to_add = pnl * (self.profit_transfer_percent / 100)
        
        # Add to first incomplete goal (by priority)
        savings_added_per_goal = {}
        
        # Sort goals by priority
        sorted_goals = sorted(
            [(k, v) for k, v in self.goals.items() if k not in self.achieved_goals],
            key=lambda x: x[1]['priority']
        )
        
        remaining = savings_to_add
        for goal_key, goal_info in sorted_goals:
            if remaining <= 0:
                break
            
            current = self.savings_accumulated.get(goal_key, 0)
            target = goal_info['target']
            needed = target - current
            
            if needed <= 0:
                continue
            
            # Add to this goal
            add = min(remaining, needed)
            self.savings_accumulated[goal_key] = current + add
            savings_added_per_goal[goal_key] = add
            remaining -= add
            
            # Check if goal achieved
            if self.savings_accumulated[goal_key] >= target:
                if goal_key not in self.achieved_goals:
                    self.achieved_goals.append(goal_key)
                    print(f"🎉 GOAL ACHIEVED: {goal_key} - {goal_info['description']}")
        
        # Update stats
        self.total_profits_tracked += pnl
        self.daily_stats['trades'] += trade_count
        self.daily_stats['pnl'] += pnl
        self.daily_stats['savings_added'] += savings_to_add
        
        self.save_state()
        return savings_added_per_goal
    
    def get_aggression_multiplier(self) -> float:
        """
        Get aggression multiplier based on goal progress
        - Within 20% of any goal: reduce aggression (0.7x)
        - Otherwise: normal (1.0x)
        """
        for goal_key in self.goals:
            if goal_key in self.achieved_goals:
                continue
            
            current = self.savings_accumulated.get(goal_key, 0)
            target = self.goals[goal_key]['target']
            
            if target > 0:
                progress = current / target
                if progress >= 0.8:  # Within 20% of goal
                    return 0.7  # Reduce aggression
        
        return 1.0  # Normal aggression
    
    def get_progress_bar(self, goal_key: str, width: int = 20) -> str:
        """Generate progress bar string for a goal"""
        if goal_key not in self.goals:
            return "[Goal not found]"
        
        target = self.goals[goal_key]['target']
        current = self.savings_accumulated.get(goal_key, 0)
        progress = min(current / target, 1.0)
        
        filled = int(progress * width)
        bar = "█" * filled + "░" * (width - filled)
        percent = progress * 100
        
        return f"[{bar}] {percent:.1f}%"
    
    def print_goal_progress(self):
        """Print all goal progress"""
        print("\n" + "=" * 50)
        print("💰 SAVINGS GOAL PROGRESS")
        print("=" * 50)
        
        for goal_key, goal_info in sorted(self.goals.items(), key=lambda x: x[1]['priority']):
            current = self.savings_accumulated.get(goal_key, 0)
            target = goal_info['target']
            achieved = "✅" if goal_key in self.achieved_goals else "⏳"
            
            bar = self.get_progress_bar(goal_key)
            print(f"{achieved} {goal_key}: ₹{current:.0f}/₹{target:.0f} {bar}")
            print(f"   {goal_info['description']}")
        
        print(f"\n📊 Total Profits Tracked: ₹{self.total_profits_tracked:.2f}")
        print(f"📊 Profit Transfer Rate: {self.profit_transfer_percent}%")
        print(f"📊 Aggression Multiplier: {self.get_aggression_multiplier()}x")
    
    def get_daily_summary(self) -> str:
        """Get daily summary string"""
        return (
            f"📊 Daily Summary:\n"
            f"   Trades: {self.daily_stats['trades']}\n"
            f"   P/L: ₹{self.daily_stats['pnl']:.2f}\n"
            f"   Savings Added: ₹{self.daily_stats['savings_added']:.2f}\n"
            f"   Aggression: {self.get_aggression_multiplier()}x"
        )
    
    def reset_daily_stats(self):
        """Reset daily stats (call at start of each day)"""
        self.daily_stats = {
            "trades": 0,
            "pnl": 0.0,
            "savings_added": 0.0
        }
        self.save_state()


# ========== STANDALONE TEST ==========
if __name__ == "__main__":
    tracker = GoalTracker()
    
    # Test adding some profits
    print("Testing GoalTracker...")
    
    # Simulate some trades
    tracker.add_profit(50.0)  # ₹50 profit
    tracker.add_profit(30.0)  # ₹30 profit
    tracker.add_profit(20.0)  # ₹20 profit
    
    tracker.print_goal_progress()
    print(tracker.get_daily_summary())