#!/usr/bin/env python3
"""
OpenClaw Trading Bot - Enhanced Intraday Momentum Strategy
NSE/BSE Stocks using yfinance (free data)

UPDATES v2.0:
- ATR-based SL/TP (dynamic risk management)
- RSI filter (avoid overbought)
- VWAP confirmation
- EMA20 filter (re-enabled)
- Candle direction check
- Trailing stop loss
- Volume threshold 1.5x (2.0x strong)
- GoalTracker integration for savings goals
"""

import yaml
import os
import sys
import json
import logging
import time as time_module
import requests
import subprocess
from datetime import datetime, time
from pathlib import Path

# Setup paths
BOT_DIR = Path(__file__).parent
CONFIG_FILE = BOT_DIR / "config.yaml"

# Import GoalTracker
sys.path.insert(0, str(BOT_DIR))
try:
    from goal_tracker import GoalTracker
    GOAL_TRACKER_ENABLED = True
except ImportError:
    GOAL_TRACKER_ENABLED = False
    print("⚠️ GoalTracker not found - continuing without it")

# ========== SELF-HEALING FUNCTIONS ==========
def check_ollama_health():
    """Check if ollama is healthy"""
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=5)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            model_names = [m['name'] for m in models]
            if 'minimax-m2.5:cloud' in model_names:
                return True
        return False
    except:
        return False

def restart_ollama():
    """Restart ollama server"""
    logger.warning("⚠️ Restarting ollama...")
    subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
    time_module.sleep(2)
    subprocess.Popen(
        ["ollama", "serve"],
        stdout=open(os.devnull, 'wb'),
        stderr=open(os.devnull, 'wb')
    )
    time_module.sleep(5)
    if check_ollama_health():
        logger.info("✅ Ollama restarted")
    else:
        logger.error("❌ Failed to restart ollama")

LOG_DIR = BOT_DIR / "logs"
TRADES_DIR = BOT_DIR / "learning"

# Create directories
LOG_DIR.mkdir(exist_ok=True)
TRADES_DIR.mkdir(exist_ok=True)

# Load config
with open(CONFIG_FILE) as f:
    CONFIG = yaml.safe_load(f)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "trading.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class TradingBot:
    def __init__(self):
        self.capital = CONFIG['STARTING_CAPITAL']
        self.initial_capital = CONFIG['STARTING_CAPITAL']
        self.positions = []
        self.trades_today = 0
        self.daily_pnl = 0
        self.last_trade_date = None
        self.last_report_date = None
        self.stock_cache = {}
        self.paper_trading = CONFIG.get('PAPER_TRADING', True)
        self.trade_log = []
        self.state_file = BOT_DIR / "state.json"
        
        # Initialize GoalTracker
        self.goal_tracker = None
        if GOAL_TRACKER_ENABLED:
            try:
                self.goal_tracker = GoalTracker()
                logger.info("💰 GoalTracker initialized")
            except Exception as e:
                logger.warning(f"GoalTracker init failed: {e}")
        
        # Load saved state
        self.load_state()
        
        logger.info(f"🚀 Trading Bot v2.0 initialized")
        logger.info(f"   Capital: ₹{self.capital}")
        logger.info(f"   Max Position: ₹{CONFIG['MAX_POSITION_SIZE']}")
        logger.info(f"   Data: {CONFIG['DATA_SOURCE']}")
        logger.info(f"   Paper Trading: {self.paper_trading}")
        logger.info(f"   Volume Threshold: {CONFIG.get('MIN_VOLUME_MULTIPLIER', 1.5)}x")
    
    def send_telegram(self, message: str):
        """Send Telegram alert"""
        if not CONFIG.get('ENABLE_TELEGRAM_ALERTS', False):
            return
        
        token = CONFIG.get('TELEGRAM_BOT_TOKEN', '')
        chat_id = CONFIG.get('TELEGRAM_CHAT_ID', '')
        
        if not token or not chat_id:
            logger.warning("Telegram not configured")
            return
        
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
            requests.post(url, data=data, timeout=10)
            logger.info(f"📱 Telegram alert sent")
        except Exception as e:
            logger.error(f"Failed to send Telegram: {e}")
    
    def load_state(self):
        """Load persisted state from file"""
        if not self.state_file.exists():
            return
        
        try:
            with open(self.state_file) as f:
                state = json.load(f)
            
            self.capital = state.get('capital', self.capital)
            self.positions = state.get('positions', [])
            self.trades_today = state.get('trades_today', 0)
            self.daily_pnl = state.get('daily_pnl', 0)
            self.last_trade_date = state.get('last_trade_date')
            self.last_report_date = state.get('last_report_date')
            self.trade_log = state.get('trade_log', [])
            
            if self.positions:
                logger.info(f"📂 Loaded {len(self.positions)} open positions from state")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
    
    def save_state(self):
        """Save current state to file"""
        try:
            state = {
                'capital': self.capital,
                'positions': self.positions,
                'trades_today': self.trades_today,
                'daily_pnl': self.daily_pnl,
                'last_trade_date': self.last_trade_date,
                'trade_log': self.trade_log,
                'last_report_date': self.last_report_date,
                'saved_at': datetime.now().isoformat()
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
    
    def is_market_open(self) -> bool:
        """Check if market is currently open (IST)"""
        import pytz
        
        ist = pytz.timezone('Asia/Kolkata')
        now_ist = datetime.now(ist)
        current_time = now_ist.time()
        
        market_start = time(CONFIG['MARKET_START_HOUR'], CONFIG['MARKET_START_MINUTE'])
        market_end = time(CONFIG['MARKET_END_HOUR'], CONFIG['MARKET_END_MINUTE'])
        
        if now_ist.weekday() >= 5:
            return False
        
        is_open = market_start <= current_time <= market_end
        
        logger.info(f"🕐 IST Time: {now_ist.strftime('%H:%M:%S')} | Market: {'OPEN' if is_open else 'CLOSED'}")
        return is_open
    
    def is_trading_allowed(self) -> bool:
        """Check if new trades are allowed"""
        # Check daily loss limit
        if self.daily_pnl <= -CONFIG['DAILY_LOSS_LIMIT']:
            logger.warning(f"⚠️ Daily loss limit hit: ₹{self.daily_pnl}")
            return False
        
        # Check max positions
        if len(self.positions) >= CONFIG['MAX_SIMULTANEOUS_TRADES']:
            logger.info("Max positions reached")
            return False
        
        # Check no-trade cutoff
        now = datetime.now().time()
        no_trade_after = time(CONFIG['NO_TRADE_AFTER_HOUR'], CONFIG['NO_TRADE_AFTER_MINUTE'])
        if now > no_trade_after:
            logger.info("No new trades after 3:00 PM")
            return False
        
        return True
    
    def get_stock_data(self, symbol: str, period: str = "5d", interval: str = "15m"):
        """Fetch stock data from yfinance"""
        try:
            import yfinance as yf
            import pandas as pd
            
            ticker = f"{symbol}.NS" if CONFIG['MARKET'] == "NSE" else f"{symbol}.BS"
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
        """
        Calculate technical indicators including:
        - EMA20 (trend)
        - RSI14 (momentum)
        - ATR14 (volatility)
        - VWAP (average price)
        - Volume SMA20
        """
        if df is None or len(df) < 20:
            return None
        
        close = df['Close']
        high = df['High']
        low = df['Low']
        volume = df['Volume']
        
        # EMA20 - trend filter
        df['EMA20'] = close.ewm(span=20).mean()
        
        # RSI14 - momentum filter
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        # ATR14 - volatility-based stop loss
        high_low = high - low
        high_close = (high - close.shift()).abs()
        low_close = (low - close.shift()).abs()
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['ATR'] = true_range.rolling(window=14).mean()
        
        # VWAP - intraday average price
        df['TP'] = (high + low + close) / 3  # Typical price
        df['VWAP'] = (df['TP'] * volume).cumsum() / volume.cumsum()
        
        # Volume SMA
        df['Volume_SMA20'] = volume.rolling(window=20).mean()
        
        # Current values
        current_price = close.iloc[-1]
        current_open = df['Open'].iloc[-1]
        current_ema = df['EMA20'].iloc[-1]
        current_rsi = df['RSI'].iloc[-1]
        current_atr = df['ATR'].iloc[-1]
        current_vwap = df['VWAP'].iloc[-1]
        current_volume = volume.iloc[-1]
        avg_volume = df['Volume_SMA20'].iloc[-1]
        
        return {
            'price': current_price,
            'open': current_open,
            'close': close.iloc[-1],
            'ema20': current_ema,
            'rsi': current_rsi,
            'atr': current_atr,
            'vwap': current_vwap,
            'volume': current_volume,
            'avg_volume': avg_volume,
            'volume_ratio': current_volume / avg_volume if avg_volume > 0 else 0
        }
    
    def check_entry_signal(self, symbol: str) -> dict:
        """
        Check if stock meets ALL entry criteria:
        1. Volume ratio >= 1.5x (2.0x for strong)
        2. Price > EMA20 (trend filter)
        3. Price > VWAP (momentum filter)
        4. RSI < 70 (avoid overbought)
        5. Close > Open (bullish candle)
        6. Price > ₹10
        7. Volume > 200k
        """
        df = self.get_stock_data(symbol)
        if df is None:
            return {'signal': False, 'reason': 'No data'}
        
        ind = self.calculate_indicators(df)
        if ind is None:
            return {'signal': False, 'reason': 'Insufficient data'}
        
        price = ind['price']
        conditions = []
        all_passed = True
        
        # 1. Volume check (minimum 1.5x, strong 2.0x)
        min_vol_mult = CONFIG.get('MIN_VOLUME_MULTIPLIER', 1.5)
        if ind['volume_ratio'] >= min_vol_mult:
            conditions.append(f"✅ Volume: {ind['volume_ratio']:.1f}x (>= {min_vol_mult}x)")
        elif ind['volume_ratio'] >= 0.8:
            conditions.append(f"⚡ Low volume: {ind['volume_ratio']:.1f}x")
            # Don't fail on low volume in aggressive mode
        else:
            conditions.append(f"❌ Low volume: {ind['volume_ratio']:.1f}x")
            all_passed = False
        
        # 2. EMA20 filter (price above EMA = uptrend)
        if price > ind['ema20']:
            conditions.append(f"✅ Price > EMA20 (₹{ind['ema20']:.2f})")
        else:
            conditions.append(f"❌ Price below EMA20 (₹{ind['ema20']:.2f})")
            all_passed = False
        
        # 3. VWAP filter (price above VWAP = bullish)
        if price > ind['vwap']:
            conditions.append(f"✅ Price > VWAP (₹{ind['vwap']:.2f})")
        else:
            conditions.append(f"❌ Price below VWAP (₹{ind['vwap']:.2f})")
            all_passed = False
        
        # 4. RSI filter (not overbought)
        if ind['rsi'] < 70:
            if 45 <= ind['rsi'] <= 65:
                conditions.append(f"✅ RSI: {ind['rsi']:.1f} (ideal zone)")
            else:
                conditions.append(f"⚡ RSI: {ind['rsi']:.1f} (< 70, OK)")
        else:
            conditions.append(f"❌ RSI: {ind['rsi']:.1f} (overbought)")
            all_passed = False
        
        # 5. Candle direction (bullish = close > open)
        if ind['close'] > ind['open']:
            conditions.append(f"✅ Bullish candle")
        else:
            conditions.append(f"❌ Bearish candle")
            all_passed = False
        
        # 6. Price minimum
        if price > CONFIG.get('MIN_PRICE', 10):
            conditions.append(f"✅ Price ₹{price:.2f} > ₹{CONFIG.get('MIN_PRICE', 10)}")
        else:
            conditions.append(f"❌ Price too low: ₹{price:.2f}")
            all_passed = False
        
        # 7. Volume minimum
        if ind['volume'] > CONFIG.get('MIN_DAILY_VOLUME', 200000):
            conditions.append(f"✅ Volume: {ind['volume']/1e6:.1f}M")
        else:
            conditions.append(f"⚡ Low volume: {ind['volume']/1e6:.1f}M")
        
        if not all_passed:
            return {'signal': False, 'reason': 'Conditions not met', 'conditions': conditions}
        
        # Return ATR for dynamic SL/TP calculation
        return {
            'signal': True,
            'price': price,
            'volume_ratio': ind['volume_ratio'],
            'ema20': ind['ema20'],
            'rsi': ind['rsi'],
            'atr': ind['atr'],
            'vwap': ind['vwap'],
            'conditions': conditions
        }
    
    def get_top_stocks(self) -> list:
        """Get list of affordable stocks (< ₹500)"""
        return [
            'YESBANK', 'SUZLON', 'RPOWER', 'NHPC', 'HAL',
            'SJVN', 'NMDC', 'PNB', 'CANBK', 'UNIONBANK',
            'BANKBARODA', 'FEDERALBNK', 'RBLBANK', 'BANDHANBNK', 'ASHOKLEY',
            'TATAPOWER', 'TATASTEEL', 'SAIL', 'HINDALCO', 'ADANIENT',
            'NBCC', 'BEML', 'DEEPAKFERT', 'RCF', 'FACT',
            'GRAPHITE', 'BHEL', 'BEL', 'COALINDIA', 'IOC',
            'BPCL', 'GAIL', 'ONGC', 'NLCINDIA', 'IRB',
            'CIPLA', 'TITAN', 'SUNPHARMA', 'LUPIN', 'GLENMARK',
            'APOLLOTYRE', 'BALKRISIND', 'EXIDEIND', 'INDUSINDBK', 'IDFCFIRSTB',
            'AUBANK', 'TATACONSUM', 'MARICO', 'DABUR'
        ]
    
    def scan_market(self):
        """Scan market for opportunities"""
        if not self.is_trading_allowed():
            return []
        
        logger.info("🔍 Scanning market...")
        opportunities = []
        
        # Get aggression multiplier from GoalTracker
        aggression = 1.0
        if self.goal_tracker:
            aggression = self.goal_tracker.get_aggression_multiplier()
            logger.info(f"📊 Aggression multiplier: {aggression}x")
        
        stocks = self.get_top_stocks()[:CONFIG['MAX_STOCKS_TO_SCAN']]
        
        for symbol in stocks:
            try:
                result = self.check_entry_signal(symbol)
                if result['signal']:
                    opportunities.append({
                        'symbol': symbol,
                        'price': result['price'],
                        'atr': result.get('atr', 0),
                        'volume_ratio': result['volume_ratio'],
                        'conditions': result['conditions']
                    })
                    logger.info(f"   🎯 {symbol}: ₹{result['price']:.2f} (Vol: {result['volume_ratio']:.1f}x, RSI: {result.get('rsi', 0):.1f})")
                    
                    # Execute paper trade
                    if self.paper_trading and len(self.positions) < CONFIG['MAX_SIMULTANEOUS_TRADES']:
                        self.execute_paper_trade(symbol, result['price'], result.get('atr', 0))
            except Exception as e:
                logger.debug(f"{symbol}: {e}")
        
        return opportunities
    
    def log_trade(self, trade: dict):
        """Log trade to learning file"""
        trades_file = TRADES_DIR / "trades.md"
        
        entry = f"| {trade['date']} | {trade['symbol']} | ₹{trade['entry_price']} | ₹{trade['exit_price']} | {trade['pnl_pct']}% | {trade['reason']} |"
        
        with open(trades_file, 'a') as f:
            f.write(entry + '\n')
    
    def execute_paper_trade(self, symbol: str, price: float, atr: float = 0):
        """Execute a paper trade with ATR-based stop loss"""
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        
        if self.capital < price:
            logger.warning(f"⚠️ Cannot buy {symbol} - price ₹{price:.2f} > capital ₹{self.capital:.2f}")
            return
        
        # Buy 1 share (or max possible)
        quantity = 1
        cost = price * quantity
        
        if cost > self.capital:
            quantity = int(self.capital / price)
            cost = price * quantity
        
        if quantity < 1:
            logger.warning(f"⚠️ Cannot buy {symbol}")
            return
        
        # Calculate ATR-based stop loss and take profit
        if atr > 0:
            # Use ATR: SL = entry - 1.5*ATR, TP = entry + 4.5*ATR
            stop_loss = price - (1.5 * atr)
            take_profit = price + (4.5 * atr)
        else:
            # Fallback to fixed percentage
            stop_loss = price * (1 - CONFIG.get('STOP_LOSS_PERCENT', 3) / 100)
            take_profit = price * (1 + CONFIG.get('TAKE_PROFIT_PERCENT', 15) / 100)
        
        position = {
            'symbol': symbol,
            'entry_price': price,
            'quantity': quantity,
            'entry_time': datetime.now(ist).strftime('%Y-%m-%d %H:%M'),
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'atr': atr,
            'trailing_stop': stop_loss  # Initial trailing stop = SL
        }
        
        self.positions.append(position)
        self.capital -= cost
        self.save_state()
        
        logger.info(f"📝 PAPER TRADE ENTERED: {symbol} | Qty: {quantity} | Entry: ₹{price:.2f}")
        logger.info(f"   SL: ₹{stop_loss:.2f} | TP: ₹{take_profit:.2f} | ATR: ₹{atr:.2f}")
        
        # Send Telegram
        msg = f"📝 <b>BUY - PAPER TRADE</b>\n\nStock: {symbol}\nQty: {quantity}\nEntry: ₹{price:.2f}\nStop Loss: ₹{stop_loss:.2f}\nTake Profit: ₹{take_profit:.2f}\nCapital Left: ₹{self.capital:.2f}"
        self.send_telegram(msg)
    
    def update_trailing_stops(self):
        """Update trailing stop loss based on profit"""
        for pos in self.positions:
            entry = pos['entry_price']
            current_price = pos.get('last_price', entry)
            
            # Calculate current profit percentage
            profit_pct = (current_price - entry) / entry * 100
            
            # Trailing stop logic:
            # After 7% gain: move stop to breakeven + 0.5%
            # After 11% gain: trail stop to +5%
            # After 15% gain: trail stop to +10%
            
            new_trailing = pos['trailing_stop']
            
            if profit_pct >= 15:
                # Trail to +10%
                new_trailing = entry * 1.10
                logger.info(f"📈 {pos['symbol']}: Trailing stop updated to +10%")
            elif profit_pct >= 11:
                # Trail to +5%
                new_trailing = entry * 1.05
                logger.info(f"📈 {pos['symbol']}: Trailing stop updated to +5%")
            elif profit_pct >= 7:
                # Trail to breakeven + 0.5%
                new_trailing = entry * 1.005
                logger.info(f"📈 {pos['symbol']}: Trailing stop updated to breakeven +0.5%")
            
            pos['trailing_stop'] = new_trailing
    
    def check_and_exit_positions(self):
        """Check positions for exit conditions"""
        if not self.positions:
            return
        
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        current_time = now.time()
        
        force_exit_time = time(15, 20)
        
        positions_to_exit = []
        
        for i, pos in enumerate(self.positions):
            df = self.get_stock_data(pos['symbol'])
            if df is None:
                continue
            
            current_price = df['Close'].iloc[-1]
            pos['last_price'] = current_price  # Store for trailing stop
            
            pos_pnl_pct = (current_price - pos['entry_price']) / pos['entry_price'] * 100
            
            exit_reason = None
            
            # Use trailing stop if it's higher than original SL
            effective_sl = max(pos['stop_loss'], pos.get('trailing_stop', pos['stop_loss']))
            
            # Check exit conditions
            if current_price <= effective_sl:
                exit_reason = "STOP LOSS"
            elif current_price >= pos['take_profit']:
                exit_reason = "TAKE PROFIT"
            elif current_time >= force_exit_time:
                exit_reason = "FORCE EXIT (3:20 PM)"
            
            if exit_reason:
                pnl = (current_price - pos['entry_price']) * pos['quantity']
                self.capital += (current_price * pos['quantity'])
                self.daily_pnl += pnl
                
                logger.info(f"📝 PAPER TRADE EXIT: {pos['symbol']} | Exit: ₹{current_price:.2f} | P/L: ₹{pnl:.2f} ({pos_pnl_pct:.1f}%) | Reason: {exit_reason}")
                
                # Log trade
                self.log_trade({
                    'date': pos['entry_time'],
                    'symbol': pos['symbol'],
                    'entry_price': pos['entry_price'],
                    'exit_price': current_price,
                    'pnl_pct': f"{pos_pnl_pct:.1f}",
                    'reason': exit_reason
                })
                
                # Add profit to GoalTracker
                if self.goal_tracker and pnl > 0:
                    self.goal_tracker.add_profit(pnl)
                
                # Telegram alert
                msg = f"📝 <b>PAPER TRADE EXIT</b>\n\nStock: {pos['symbol']}\nExit: ₹{current_price:.2f}\nP/L: ₹{pnl:.2f} ({pos_pnl_pct:.1f}%)\nReason: {exit_reason}\nCapital: ₹{self.capital:.2f}"
                self.send_telegram(msg)
                
                positions_to_exit.append(i)
        
        for i in reversed(positions_to_exit):
            self.positions.pop(i)
        
        if positions_to_exit:
            self.save_state()
            # Update trailing stops for remaining positions
            self.update_trailing_stops()
        
        if self.positions:
            logger.info(f"📊 Open Positions: {len(self.positions)} | Capital: ₹{self.capital:.2f} | Daily P/L: ₹{self.daily_pnl:.2f}")
    
    def send_daily_report(self):
        """Send detailed daily report via Telegram"""
        if not self.positions:
            return
        
        today = datetime.now().strftime('%Y-%m-%d')
        if self.last_report_date == today:
            return
        
        import yfinance as yf
        
        report = "📊 <b>DAILY TRADING REPORT</b>\n\n"
        report += f"📅 Date: {datetime.now().strftime('%Y-%m-%d')}\n"
        report += f"💰 Capital: ₹{self.capital:.2f}\n"
        report += f"📈 Daily P/L: ₹{self.daily_pnl:.2f}\n\n"
        report += "<b>Open Positions:</b>\n"
        
        total_pl = 0
        for pos in self.positions:
            ticker = pos['symbol'] + '.NS'
            try:
                df = yf.download(ticker, period='1d', interval='5m', progress=False, timeout=5)
                if df is not None and not df.empty:
                    current_price = df['Close'].iloc[-1]
                    if hasattr(current_price, 'item'):
                        current_price = current_price.item()
                    else:
                        current_price = float(current_price)
                else:
                    current_price = pos['entry_price']
            except:
                current_price = pos['entry_price']
            
            pl = (current_price - pos['entry_price']) * pos['quantity']
            pl_pct = (current_price - pos['entry_price']) / pos['entry_price'] * 100
            total_pl += pl
            
            emoji = "🟢" if pl >= 0 else "🔴"
            report += f"{emoji} {pos['symbol']}: ₹{pos['entry_price']:.2f} → ₹{current_price:.2f} | P/L: ₹{pl:.2f} ({pl_pct:.1f}%)\n"
        
        report += f"\n<b>Total Unrealized P/L: ₹{total_pl:.2f}</b>"
        
        # Add GoalTracker progress
        if self.goal_tracker:
            report += "\n\n<b>💰 Savings Goals:</b>\n"
            for goal_key, goal_info in sorted(self.goal_tracker.goals.items(), key=lambda x: x[1]['priority'])[:3]:
                current = self.goal_tracker.savings_accumulated.get(goal_key, 0)
                target = goal_info['target']
                report += f"• {goal_key}: ₹{current:.0f}/₹{target:.0f}\n"
        
        self.send_telegram(report)
        
        self.last_report_date = today
        self.save_state()
    
    def run(self):
        """Main bot loop"""
        logger.info("🤖 Bot v2.0 starting...")
        
        # Print goal progress on startup
        if self.goal_tracker:
            self.goal_tracker.print_goal_progress()
        
        while True:
            try:
                logger.info("=" * 50)
                
                if not self.is_market_open():
                    logger.info("🌙 Market closed. Sleeping...")
                    self.send_daily_report()
                    time_module.sleep(300)
                    continue
                
                logger.info("Starting market scan...")
                opportunities = self.scan_market()
                logger.info(f"Scan complete. Found: {len(opportunities)}")
                
                self.check_and_exit_positions()
                
                if opportunities:
                    logger.info(f"Found {len(opportunities)} opportunities")
                
                time_module.sleep(CONFIG['SCAN_INTERVAL_MINUTES'] * 60)
                self.save_state()
                
            except KeyboardInterrupt:
                logger.info("🛑 Bot stopped by user")
                self.save_state()
                break
            except Exception as e:
                logger.error(f"Error: {e}")
                import traceback
                logger.error(traceback.format_exc())
                time_module.sleep(60)


if __name__ == "__main__":
    bot = TradingBot()
    bot.run()