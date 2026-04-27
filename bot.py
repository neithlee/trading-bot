#!/usr/bin/env python3
"""
Trading Bot v3.0 - Compound Growth Mode
MISSION: Grow ₹500 → ₹5000 with aggressive reinvestment
"""

import yaml
import os
import sys
import json
import logging
import time as time_module
import requests
import subprocess
from datetime import datetime, time, timedelta
from pathlib import Path

BOT_DIR = Path(__file__).parent
CONFIG_FILE = BOT_DIR / "config.yaml"

LOG_DIR = BOT_DIR / "logs"
TRADES_DIR = BOT_DIR / "learning"
LOG_DIR.mkdir(exist_ok=True)
TRADES_DIR.mkdir(exist_ok=True)

with open(CONFIG_FILE) as f:
    CONFIG = yaml.safe_load(f)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "trading.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ===== COMPOUND GROWTH CONFIG =====
COMPOUND_TARGET = 5000  # Target to switch to savings mode
SAVINGS_MODE_THRESHOLD = 5000
MIN_CAPITAL_UTILIZATION = 0.80  # Deploy 80% minimum

# Stock Universe - All 3 Tiers
STOCK_UNIVERSE = {
    'Tier1': ['YESBANK', 'SUZLON', 'VODAFONE', 'IDEA', 'IRFC', 'NHPC', 'SJVN', 'RVNL', 'IRCON', 'NBCC'],
    'Tier2': ['TATAPOWER', 'ADANIGREEN', 'ADANIPORTS', 'CANBK', 'UNIONBANK', 'BANKBARODA', 'PNB', 'SAIL', 'NMDC'],
    'Tier3': ['ZOMATO', 'PAYTM', 'DELHIVERY', 'NYKAA', 'POLICYBZR']
}


class TradingBot:
    def __init__(self):
        self.capital = CONFIG['STARTING_CAPITAL']
        self.initial_capital = CONFIG['STARTING_CAPITAL']
        self.positions = []
        self.daily_pnl = 0
        self.last_report_date = None
        self.state_file = BOT_DIR / "state.json"
        
        # Compound mode tracking
        self.compound_mode = True  # True until ₹5000
        self.total_profits_reinvested = 0
        
        self.load_state()
        
        logger.info(f"🚀 Trading Bot v3.0 - COMPOUND MODE")
        logger.info(f"   Starting Capital: ₹{self.initial_capital}")
        logger.info(f"   Target: ₹{COMPOUND_TARGET}")
        logger.info(f"   Compound Mode: {self.compound_mode}")
    
    def send_telegram(self, message: str):
        if not CONFIG.get('ENABLE_TELEGRAM_ALERTS', False):
            return
        token = CONFIG.get('TELEGRAM_BOT_TOKEN', '')
        chat_id = CONFIG.get('TELEGRAM_CHAT_ID', '')
        if not token or not chat_id:
            return
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            requests.post(url, data={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=10)
        except Exception as e:
            logger.error(f"Telegram error: {e}")
    
    def load_state(self):
        if not self.state_file.exists():
            return
        try:
            with open(self.state_file) as f:
                state = json.load(f)
            self.capital = state.get('capital', self.capital)
            self.positions = state.get('positions', [])
            self.daily_pnl = state.get('daily_pnl', 0)
            self.last_report_date = state.get('last_report_date')
            
            # Check if we hit compound target
            if self.capital >= COMPOUND_TARGET:
                self.compound_mode = False
                logger.info(f"🎉 COMPOUND TARGET REACHED! Switching to savings mode")
        except Exception as e:
            logger.error(f"Load state error: {e}")
    
    def save_state(self):
        try:
            state = {
                'capital': self.capital,
                'positions': self.positions,
                'daily_pnl': self.daily_pnl,
                'last_report_date': self.last_report_date,
                'compound_mode': self.compound_mode,
                'total_profits_reinvested': self.total_profits_reinvested,
                'saved_at': datetime.now().isoformat()
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Save state error: {e}")
    
    def is_market_open(self) -> bool:
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        now_ist = datetime.now(ist)
        current_time = now_ist.time()
        market_start = time(9, 15)
        market_end = time(15, 20)
        if now_ist.weekday() >= 5:
            return False
        return market_start <= current_time <= market_end
    
    def is_trading_allowed(self) -> bool:
        if self.daily_pnl <= -CONFIG['DAILY_LOSS_LIMIT']:
            logger.warning(f"⚠️ Daily loss limit hit: ₹{self.daily_pnl}")
            return False
        if len(self.positions) >= CONFIG['MAX_SIMULTANEOUS_TRADES']:
            return False
        now = datetime.now().time()
        if now > time(14, 30):  # Stop new trades by 2:30 PM
            return False
        return True
    
    def get_stock_data(self, symbol: str, period: str = "5d", interval: str = "15m"):
        try:
            import yfinance as yf
            import pandas as pd
            ticker = f"{symbol}.NS"
            df = yf.download(ticker, period=period, interval=interval, progress=False, timeout=5)
            if df is None or df.empty:
                return None
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]
            return df
        except Exception as e:
            logger.debug(f"Error fetching {symbol}: {e}")
            return None
    
    def calculate_indicators(self, df):
        if df is None or len(df) < 25:
            return None
        
        close = df['Close']
        high = df['High']
        low = df['Low']
        volume = df['Volume']
        
        # EMA9, EMA20, EMA21
        df['EMA9'] = close.ewm(span=9).mean()
        df['EMA20'] = close.ewm(span=20).mean()
        df['EMA21'] = close.ewm(span=21).mean()
        
        # RSI
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        # ATR
        high_low = high - low
        high_close = (high - close.shift()).abs()
        low_close = (low - close.shift()).abs()
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['ATR'] = true_range.rolling(window=14).mean()
        
        # VWAP
        df['TP'] = (high + low + close) / 3
        df['VWAP'] = (df['TP'] * volume).cumsum() / volume.cumsum()
        
        # Volume SMA
        df['Volume_SMA20'] = volume.rolling(window=20).mean()
        
        # Opening Range (first 15 min)
        if len(df) >= 2:
            df['ORB_HIGH'] = df['High'].iloc[0]
            df['ORB_LOW'] = df['Low'].iloc[0]
        
        return {
            'price': close.iloc[-1],
            'open': df['Open'].iloc[-1],
            'close': close.iloc[-1],
            'high': high.iloc[-1],
            'low': low.iloc[-1],
            'ema9': df['EMA9'].iloc[-1],
            'ema20': df['EMA20'].iloc[-1],
            'ema21': df['EMA21'].iloc[-1],
            'rsi': df['RSI'].iloc[-1],
            'atr': df['ATR'].iloc[-1],
            'vwap': df['VWAP'].iloc[-1],
            'volume': volume.iloc[-1],
            'avg_volume': df['Volume_SMA20'].iloc[-1],
            'volume_ratio': volume.iloc[-1] / df['Volume_SMA20'].iloc[-1] if df['Volume_SMA20'].iloc[-1] > 0 else 0,
            'bullish_candle': close.iloc[-1] > df['Open'].iloc[-1]
        }
    
    def score_signal(self, ind: dict) -> tuple:
        """Score trade 0-100, return (score, position_size_pct)"""
        score = 0
        
        # Volume scoring
        if ind['volume_ratio'] >= 1.5:
            score += 20
        if ind['volume_ratio'] >= 2.0:
            score += 10  # Bonus
        
        # Trend confirmation
        if ind['price'] > ind['ema20']:
            score += 15
        if ind['price'] > ind['vwap']:
            score += 15
        
        # RSI zone
        if 45 <= ind['rsi'] <= 65:
            score += 10
        elif ind['rsi'] < 70:
            score += 5
        
        # Candle
        if ind['bullish_candle']:
            score += 10
        
        # EMA crossover
        if ind['ema9'] > ind['ema21']:
            score += 10
        
        # Basic filters
        if ind['price'] < 5 or ind['volume'] < 500000:
            score = 0
        
        # Position sizing
        if score >= 80:
            pos_size = 0.25  # 25% for high confidence
        elif score >= 60:
            pos_size = 0.15  # 15% standard
        else:
            pos_size = 0
        
        return score, pos_size
    
    def check_entry_signal(self, symbol: str) -> dict:
        df = self.get_stock_data(symbol)
        if df is None:
            return {'signal': False}
        
        ind = self.calculate_indicators(df)
        if ind is None:
            return {'signal': False}
        
        score, pos_size = self.score_signal(ind)
        
        if score >= 60:
            return {
                'signal': True,
                'score': score,
                'position_size_pct': pos_size,
                'price': ind['price'],
                'atr': ind['atr'],
                'volume_ratio': ind['volume_ratio'],
                'rsi': ind['rsi'],
                'ema20': ind['ema20'],
                'vwap': ind['vwap'],
                'stop_loss': ind['price'] - (1.5 * ind['atr']),
                'take_profit': ind['price'] + (3 * ind['atr'])  # 3x ATR for target
            }
        return {'signal': False, 'score': score}
    
    def get_max_position_size(self) -> float:
        """Calculate max position size based on capital and compound mode"""
        available = self.capital
        
        # In compound mode: deploy 80%+ 
        # Max per position = 20% of current capital
        max_per_position = available * 0.20
        
        # Scale up as capital grows (but cap at ₹200 for safety until ₹2000)
        if self.capital < 2000:
            max_per_position = min(max_per_position, 100)
        
        return max_per_position
    
    def get_available_capital(self) -> float:
        """Get capital available for new positions"""
        # Already deployed in positions
        deployed = sum(p['entry_price'] * p['quantity'] for p in self.positions)
        return self.capital - deployed
    
    def can_add_position(self, symbol: str) -> bool:
        """Check if we can add a new position (no duplicates, capital available)"""
        # No duplicate positions
        for pos in self.positions:
            if pos['symbol'] == symbol:
                return False
        
        # Check capital utilization
        available = self.get_available_capital()
        min_utilization = self.capital * MIN_CAPITAL_UTILIZATION
        
        if available < min_utilization:
            return False
        
        return True
    
    def execute_paper_trade(self, symbol: str, price: float, atr: float, score: int, pos_pct: float):
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        
        # Calculate position size
        max_size = self.get_max_position_size()
        position_capital = self.capital * pos_pct
        
        # Determine quantity
        quantity = int(position_capital / price)
        if quantity < 1:
            quantity = 1
        
        cost = price * quantity
        if cost > self.capital:
            quantity = int(self.capital / price)
            cost = price * quantity
        
        if quantity < 1 or cost > self.capital:
            logger.warning(f"Cannot buy {symbol}")
            return
        
        # Calculate stops
        stop_loss = price - (1.5 * atr) if atr > 0 else price * 0.97
        take_profit = price + (3 * atr) if atr > 0 else price * 1.10
        
        position = {
            'symbol': symbol,
            'entry_price': price,
            'quantity': quantity,
            'entry_time': datetime.now(ist).strftime('%Y-%m-%d %H:%M'),
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'atr': atr,
            'score': score,
            'trailing_stop': stop_loss,
            'initial_stop': stop_loss
        }
        
        self.positions.append(position)
        self.capital -= cost
        
        logger.info(f"📝 BUY {symbol} | Qty: {quantity} | Entry: ₹{price:.2f} | Score: {score} | SL: ₹{stop_loss:.2f}")
        
        self.save_state()
        
        msg = f"📈 <b>BUY {symbol}</b>\n\nEntry: ₹{price:.2f}\nQty: {quantity}\nScore: {score}\nCapital: ₹{self.capital:.2f}"
        self.send_telegram(msg)
    
    def update_trailing_stops(self):
        """Update trailing stops based on profit"""
        for pos in self.positions:
            entry = pos['entry_price']
            current = pos.get('last_price', entry)
            profit_pct = (current - entry) / entry * 100
            
            if profit_pct >= 20:
                # Close 50%, trail rest
                pos['trailing_stop'] = entry * 1.08
            elif profit_pct >= 15:
                pos['trailing_stop'] = entry * 1.08
            elif profit_pct >= 10:
                pos['trailing_stop'] = entry * 1.04
            elif profit_pct >= 5:
                pos['trailing_stop'] = entry  # Breakeven
            else:
                pos['trailing_stop'] = pos['initial_stop']
    
    def check_and_exit_positions(self):
        if not self.positions:
            return
        
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        
        positions_to_exit = []
        
        for i, pos in enumerate(self.positions):
            df = self.get_stock_data(pos['symbol'])
            if df is None:
                continue
            
            current_price = df['Close'].iloc[-1]
            pos['last_price'] = current_price
            
            pos_pnl_pct = (current_price - pos['entry_price']) / pos['entry_price'] * 100
            effective_sl = max(pos['stop_loss'], pos.get('trailing_stop', pos['stop_loss']))
            
            exit_reason = None
            
            # Check exits
            if current_price <= effective_sl:
                exit_reason = "STOP LOSS"
            elif current_price >= pos['take_profit']:
                exit_reason = "TAKE PROFIT"
            elif datetime.now(ist).time() >= time(15, 20):
                exit_reason = "FORCE EXIT"
            
            if exit_reason:
                pnl = (current_price - pos['entry_price']) * pos['quantity']
                self.capital += (current_price * pos['quantity'])
                self.daily_pnl += pnl
                
                # Compound mode: 100% reinvest
                if self.compound_mode:
                    self.total_profits_reinvested += max(0, pnl)
                    logger.info(f"💰 Profit ₹{pnl:.2f} reinvested (Total: ₹{self.total_profits_reinvested:.2f})")
                
                logger.info(f"📝 EXIT {pos['symbol']} | Exit: ₹{current_price:.2f} | P/L: ₹{pnl:.2f} ({pos_pnl_pct:.1f}%) | {exit_reason}")
                
                msg = f"📉 <b>EXIT {pos['symbol']}</b>\n\nExit: ₹{current_price:.2f}\nP/L: ₹{pnl:.2f} ({pos_pnl_pct:.1f}%)\nCapital: ₹{self.capital:.2f}"
                self.send_telegram(msg)
                
                positions_to_exit.append(i)
        
        for i in reversed(positions_to_exit):
            self.positions.pop(i)
        
        if positions_to_exit:
            self.save_state()
            self.update_trailing_stops()
        
        if self.positions:
            logger.info(f"📊 Positions: {len(self.positions)} | Capital: ₹{self.capital:.2f} | Daily P/L: ₹{self.daily_pnl:.2f}")
    
    def scan_market(self):
        if not self.is_trading_allowed():
            return []
        
        logger.info("🔍 Scanning market...")
        opportunities = []
        
        # Get all stocks from all tiers
        all_stocks = []
        for tier in STOCK_UNIVERSE.values():
            all_stocks.extend(tier)
        
        # Shuffle for variety
        import random
        random.shuffle(all_stocks)
        
        for symbol in all_stocks[:30]:  # Scan top 30
            try:
                if not self.can_add_position(symbol):
                    continue
                
                result = self.check_entry_signal(symbol)
                if result['signal']:
                    opportunities.append({
                        'symbol': symbol,
                        'price': result['price'],
                        'score': result['score'],
                        'position_size_pct': result['position_size_pct'],
                        'atr': result.get('atr', 0)
                    })
                    logger.info(f"   🎯 {symbol} | Score: {result['score']} | Price: ₹{result['price']:.2f}")
                    
                    # Execute trade
                    self.execute_paper_trade(
                        symbol,
                        result['price'],
                        result.get('atr', 0),
                        result['score'],
                        result['position_size_pct']
                    )
            except Exception as e:
                logger.debug(f"{symbol}: {e}")
        
        return opportunities
    
    def run(self):
        logger.info("🤖 Bot v3.0 starting...")
        
        while True:
            try:
                logger.info("=" * 50)
                
                if not self.is_market_open():
                    logger.info("🌙 Market closed. Sleeping...")
                    time_module.sleep(300)
                    continue
                
                opportunities = self.scan_market()
                
                self.check_and_exit_positions()
                
                # Check compound mode
                if self.compound_mode and self.capital >= COMPOUND_TARGET:
                    self.compound_mode = False
                    logger.info(f"🎉 COMPOUND TARGET ₹{COMPOUND_TARGET} REACHED!")
                    logger.info("💰 Now switching to savings mode (30% profit transfer)")
                    self.send_telegram(f"🎉 COMPOUND TARGET REACHED! Capital: ₹{self.capital:.2f}")
                
                time_module.sleep(CONFIG['SCAN_INTERVAL_MINUTES'] * 60)
                self.save_state()
                
            except KeyboardInterrupt:
                logger.info("🛑 Bot stopped")
                self.save_state()
                break
            except Exception as e:
                logger.error(f"Error: {e}")
                time_module.sleep(60)


if __name__ == "__main__":
    bot = TradingBot()
    bot.run()