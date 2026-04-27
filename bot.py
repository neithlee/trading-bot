#!/usr/bin/env python3
"""
Trading Bot v3.1 - Compound Growth Mode with GrowthTracker
MISSION: Grow ₹500 → ₹5000 (Phase 1) → ₹50000 (Phase 2)
"""

import yaml
import os
import sys
import json
import logging
import time as time_module
import requests
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


# ===== STOCK UNIVERSE =====
STOCK_UNIVERSE = {
    'Tier1': ['YESBANK', 'SUZLON', 'VODAFONE', 'IDEA', 'IRFC', 'NHPC', 'SJVN', 'RVNL', 'IRCON', 'NBCC'],
    'Tier2': ['TATAPOWER', 'ADANIGREEN', 'ADANIPORTS', 'CANBK', 'UNIONBANK', 'BANKBARODA', 'PNB', 'SAIL', 'NMDC'],
    'Tier3': ['ZOMATO', 'PAYTM', 'DELHIVERY', 'NYKAA', 'POLICYBZR']
}

# Import GrowthTracker
sys.path.insert(0, str(BOT_DIR))
try:
    from growth_tracker import GrowthTracker
except ImportError:
    GrowthTracker = None


class TradingBot:
    def __init__(self):
        self.capital = CONFIG['STARTING_CAPITAL']
        self.initial_capital = CONFIG['STARTING_CAPITAL']
        self.positions = []
        self.daily_pnl = 0
        self.daily_trades = []
        self.state_file = BOT_DIR / "state.json"
        
        # Growth tracker
        self.growth = GrowthTracker() if GrowthTracker else None
        
        # Phase tracking
        self.phase = 1  # 1 = compound, 2 = savings
        
        self.load_state()
        
        self.send_telegram("🤖 Trading Bot v3.1 Started!\n" + 
                          f"Capital: ₹{self.capital}\n" +
                          f"Phase: {self.phase}")
    
    def send_telegram(self, message: str):
        """Send Telegram alert"""
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
            logger.error(f"Telegram: {e}")
    
    def load_state(self):
        """Load state from file"""
        if not self.state_file.exists():
            return
        try:
            with open(self.state_file) as f:
                state = json.load(f)
            self.capital = state.get('capital', self.capital)
            self.positions = state.get('positions', [])
            self.daily_pnl = state.get('daily_pnl', 0)
            self.phase = state.get('phase', 1)
            
            if self.growth:
                self.growth.current_capital = self.capital
        except Exception as e:
            logger.error(f"Load error: {e}")
    
    def save_state(self):
        """Save state to file"""
        try:
            state = {
                'capital': self.capital,
                'positions': self.positions,
                'daily_pnl': self.daily_pnl,
                'phase': self.phase,
                'saved_at': datetime.now().isoformat()
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Save error: {e}")
    
    def is_market_open(self) -> bool:
        """Check if market is open (IST)"""
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        now_ist = datetime.now(ist)
        current_time = now_ist.time()
        market_start = time(9, 15)
        market_end = time(15, 20)
        
        if now_ist.weekday() >= 5:  # Weekend
            return False
        return market_start <= current_time <= market_end
    
    def is_trading_allowed(self) -> bool:
        """Check if new trades allowed"""
        # Daily loss limit = 15% of capital
        loss_limit = self.capital * 0.15
        if self.daily_pnl <= -loss_limit:
            logger.warning(f"⚠️ Daily loss limit hit: ₹{self.daily_pnl}")
            return False
        
        # Max positions
        if len(self.positions) >= CONFIG['MAX_SIMULTANEOUS_TRADES']:
            return False
        
        # No new trades after 14:30
        now = datetime.now().time()
        if now > time(14, 30):
            return False
        
        # Capital pause check
        if self.capital < 200:
            logger.warning(f"⚠️ Capital below ₹200 - pausing")
            self.send_telegram(f"⚠️ CAPITAL ALERT: ₹{self.capital:.2f} - Pausing trades")
            return False
        
        return True
    
    def get_stock_data(self, symbol: str, period: str = "5d", interval: str = "15m"):
        """Fetch stock data from yfinance"""
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
            logger.debug(f"Fetch error {symbol}: {e}")
            return None
    
    def calculate_indicators(self, df):
        """Calculate all technical indicators"""
        if df is None or len(df) < 25:
            return None
        
        close = df['Close']
        high = df['High']
        low = df['Low']
        volume = df['Volume']
        
        # EMAs
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
            'bullish_candle': close.iloc[-1] > df['Open'].iloc[-1],
            'ema20_slope': df['EMA20'].iloc[-1] - df['EMA20'].iloc[-2]
        }
    
    def check_volume_momentum(self, ind: dict) -> dict:
        """Strategy 1: Volume Momentum"""
        score = 0
        signal = False
        
        # Volume check
        if ind['volume_ratio'] >= 1.5:
            score += 20
            signal = True
        if ind['volume_ratio'] >= 2.0:
            score += 10
        
        # Price > EMA20 (trend)
        if ind['price'] > ind['ema20']:
            score += 15
        
        # Price > VWAP
        if ind['price'] > ind['vwap']:
            score += 15
        
        # RSI 45-68
        if 45 <= ind['rsi'] <= 68:
            score += 10
        elif ind['rsi'] < 70:
            score += 5
        
        # Bullish candle
        if ind['bullish_candle']:
            score += 10
        
        # Basic filters
        if ind['price'] < 5 or ind['volume'] < 500000:
            return {'signal': False}
        
        return {'signal': signal, 'score': score, 'strategy': 'Volume Momentum'}
    
    def check_vwap_bounce(self, ind: dict) -> dict:
        """Strategy 2: VWAP Bounce"""
        price = ind['price']
        vwap = ind['vwap']
        
        # Price touches VWAP from above (within 0.3%)
        if vwap * 0.997 <= price <= vwap * 1.003:
            # RSI not oversold
            if ind['rsi'] > 40:
                # Volume spike
                if ind['volume_ratio'] > 1.2:
                    # EMA20 uptrend
                    if ind['ema20_slope'] > 0:
                        return {'signal': True, 'score': 65, 'strategy': 'VWAP Bounce'}
        
        return {'signal': False}
    
    def check_orb_breakout(self, df, ind: dict) -> dict:
        """Strategy 3: Opening Range Breakout"""
        # Need 15min data for ORB
        if df is None or len(df) < 3:
            return {'signal': False}
        
        # First candle is 9:15-9:30
        high_15m = df['High'].iloc[0]
        low_15m = df['Low'].iloc[0]
        
        current_price = ind['price']
        
        # Breakout above 15min high with volume
        if current_price > high_15m and ind['volume_ratio'] > 2.0:
            if ind['rsi'] < 75:
                return {'signal': True, 'score': 70, 'strategy': 'ORB Breakout', 
                        'orb_high': high_15m, 'orb_low': low_15m}
        
        return {'signal': False}
    
    def check_ema_crossover(self, ind: dict) -> dict:
        """Strategy 4: EMA Crossover Scalp"""
        # EMA9 crosses above EMA21
        if ind['ema9'] > ind['ema21']:
            # Volume at crossover
            if ind['volume_ratio'] > 1.3:
                # Price and RSI filters
                if ind['price'] > 5 and 40 <= ind['rsi'] <= 65:
                    return {'signal': True, 'score': 60, 'strategy': 'EMA Crossover'}
        
        return {'signal': False}
    
    def score_trade(self, symbol: str, df, ind: dict) -> dict:
        """Combine all strategies - best signal wins"""
        strategies = []
        
        # Check all strategies
        vm = self.check_volume_momentum(ind)
        if vm['signal']:
            strategies.append(vm)
        
        vwap = self.check_vwap_bounce(ind)
        if vwap['signal']:
            strategies.append(vwap)
        
        orb = self.check_orb_breakout(df, ind)
        if orb['signal']:
            strategies.append(orb)
        
        ema = self.check_ema_crossover(ind)
        if ema['signal']:
            strategies.append(ema)
        
        if not strategies:
            return {'signal': False, 'score': 0}
        
        # Pick highest score
        best = max(strategies, key=lambda x: x['score'])
        
        # Position sizing
        if best['score'] >= 80:
            pos_pct = 0.25
        elif best['score'] >= 60:
            pos_pct = 0.20  # 20% for standard
        else:
            pos_pct = 0
        
        return {
            'signal': True,
            'score': best['score'],
            'strategy': best['strategy'],
            'position_size_pct': pos_pct,
            'price': ind['price'],
            'atr': ind['atr'],
            'rsi': ind['rsi'],
            'volume_ratio': ind['volume_ratio']
        }
    
    def get_position_size(self) -> float:
        """Calculate position size based on capital and phase"""
        open_count = len(self.positions)
        
        if self.growth:
            size = self.growth.get_position_size(self.capital, open_count)
        else:
            # Fallback: 20% of capital
            size = self.capital * 0.20
            if open_count > 0:
                size = min(size, self.capital / open_count)
        
        # Phase 2: reduce to 15%
        if self.phase == 2:
            size = self.capital * 0.15
        
        return size
    
    def can_add_position(self, symbol: str) -> bool:
        """Check if we can add position"""
        # No duplicates
        for pos in self.positions:
            if pos['symbol'] == symbol:
                return False
        
        # Capital utilization check (min 80%)
        deployed = sum(p['entry_price'] * p['quantity'] for p in self.positions)
        available = self.capital - deployed
        min_util = self.capital * 0.80
        
        if available < min_util and len(self.positions) < 2:
            # Allow if we have fewer than 2 positions
            return True
        
        return available >= self.get_position_size() * 0.5
    
    def execute_paper_trade(self, symbol: str, price: float, atr: float, score: int, strategy: str):
        """Execute a paper trade"""
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        
        position_capital = self.get_position_size()
        quantity = int(position_capital / price)
        
        if quantity < 1:
            quantity = 1
        
        cost = price * quantity
        if cost > self.capital:
            quantity = int(self.capital / price)
            cost = price * quantity
        
        if quantity < 1:
            return
        
        # Calculate stops
        stop_loss = price - (1.5 * atr) if atr > 0 else price * 0.95
        take_profit = price + (3 * atr) if atr > 0 else price * 1.10
        
        position = {
            'symbol': symbol,
            'entry_price': price,
            'quantity': quantity,
            'entry_time': datetime.now(ist).strftime('%Y-%m-%d %H:%M'),
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'atr': atr,
            'strategy': strategy,
            'score': score,
            'trailing_stop': stop_loss,
            'initial_stop': stop_loss
        }
        
        self.positions.append(position)
        self.capital -= cost
        
        logger.info(f"📝 {strategy} | BUY {symbol} | Qty: {quantity} | Entry: ₹{price:.2f} | Score: {score}")
        
        self.save_state()
        
        msg = f"📈 <b>BUY {symbol}</b>\nStrategy: {strategy}\nEntry: ₹{price:.2f}\nQty: {quantity}\nScore: {score}\nCapital: ₹{self.capital:.2f}"
        self.send_telegram(msg)
    
    def update_trailing_stops(self):
        """Update trailing stops"""
        for pos in self.positions:
            entry = pos['entry_price']
            current = pos.get('last_price', entry)
            profit_pct = (current - entry) / entry * 100
            
            if profit_pct >= 20:
                pos['trailing_stop'] = entry * 1.08
            elif profit_pct >= 15:
                pos['trailing_stop'] = entry * 1.08
            elif profit_pct >= 10:
                pos['trailing_stop'] = entry * 1.04
            elif profit_pct >= 5:
                pos['trailing_stop'] = entry
            else:
                pos['trailing_stop'] = pos['initial_stop']
    
    def check_and_exit_positions(self):
        """Check and exit positions"""
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
            
            pnl_pct = (current_price - pos['entry_price']) / pos['entry_price'] * 100
            effective_sl = max(pos['stop_loss'], pos.get('trailing_stop', pos['stop_loss']))
            
            exit_reason = None
            
            if current_price <= effective_sl:
                exit_reason = "STOP LOSS"
            elif current_price >= pos['take_profit']:
                exit_reason = "TAKE PROFIT"
            elif datetime.now(ist).time() >= time(15, 20):
                exit_reason = "FORCE EXIT"
            
            if exit_reason:
                pnl = (current_price - pos['entry_price']) * pos['quantity']
                self.capital += current_price * pos['quantity']
                self.daily_pnl += pnl
                
                # Record trade
                trade = {
                    'symbol': pos['symbol'],
                    'pnl': pnl,
                    'strategy': pos.get('strategy', 'Unknown'),
                    'time': datetime.now().isoformat()
                }
                self.daily_trades.append(trade)
                
                # Update growth tracker
                if self.growth:
                    self.growth.record_trade(pnl, pos['symbol'])
                
                logger.info(f"📝 EXIT {pos['symbol']} | P/L: ₹{pnl:.2f} ({pnl_pct:.1f}%) | {exit_reason}")
                
                msg = f"📉 <b>EXIT {pos['symbol']}</b>\nP/L: ₹{pnl:.2f} ({pnl_pct:.1f}%)\nCapital: ₹{self.capital:.2f}"
                self.send_telegram(msg)
                
                positions_to_exit.append(i)
        
        for i in reversed(positions_to_exit):
            self.positions.pop(i)
        
        if positions_to_exit:
            self.save_state()
            self.update_trailing_stops()
    
    def check_phase_switch(self):
        """Check and switch phases"""
        if self.phase == 1 and self.capital >= 5000:
            self.phase = 2
            logger.info("🎉 PHASE 2 ACTIVATED - Savings mode ON!")
            self.send_telegram("🎉 PHASE 2 UNLOCKED!\n" +
                              "Savings mode: 30% to savings\n" +
                              f"Capital: ₹{self.capital:.2f}")
    
    def scan_market(self):
        """Scan market for opportunities"""
        if not self.is_trading_allowed():
            return []
        
        logger.info("🔍 Scanning market...")
        opportunities = []
        
        # Get all stocks
        all_stocks = []
        for tier in STOCK_UNIVERSE.values():
            all_stocks.extend(tier)
        
        import random
        random.shuffle(all_stocks)
        
        for symbol in all_stocks[:30]:
            try:
                if not self.can_add_position(symbol):
                    continue
                
                df = self.get_stock_data(symbol)
                if df is None:
                    continue
                
                ind = self.calculate_indicators(df)
                if ind is None:
                    continue
                
                result = self.score_trade(symbol, df, ind)
                
                if result['signal']:
                    opportunities.append({
                        'symbol': symbol,
                        'strategy': result['strategy'],
                        'score': result['score'],
                        'position_size_pct': result['position_size_pct'],
                        'price': result['price'],
                        'atr': result.get('atr', 0)
                    })
                    
                    logger.info(f"   🎯 {symbol} | {result['strategy']} | Score: {result['score']}")
                    
                    # Execute trade
                    self.execute_paper_trade(
                        symbol,
                        result['price'],
                        result.get('atr', 0),
                        result['score'],
                        result['strategy']
                    )
                    
                    # Check phase switch after trade
                    self.check_phase_switch()
                    
            except Exception as e:
                logger.debug(f"{symbol}: {e}")
        
        return opportunities
    
    def send_daily_report(self):
        """Send daily report at 15:25"""
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        
        if datetime.now(ist).time() < time(15, 20):
            return
        
        if self.growth:
            self.growth.print_daily_report(self.daily_trades)
            self.growth.add_daily_pnl(self.daily_pnl)
        
        self.daily_trades = []
        self.daily_pnl = 0
        self.save_state()
    
    def run(self):
        """Main loop"""
        logger.info("🤖 Bot v3.1 starting...")
        
        while True:
            try:
                logger.info("=" * 50)
                
                if not self.is_market_open():
                    logger.info("🌙 Market closed. Sleeping...")
                    time_module.sleep(300)
                    continue
                
                opportunities = self.scan_market()
                self.check_and_exit_positions()
                
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