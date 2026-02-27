#!/usr/bin/env python3
"""
OpenClaw Trading Bot - Intraday Momentum Strategy
NSE/BSE Stocks using yfinance (free data)
Credit-optimized mode
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
        self.stock_cache = {}
        self.paper_trading = CONFIG.get('PAPER_TRADING', True)
        self.trade_log = []
        
        logger.info(f"🚀 Trading Bot initialized")
        logger.info(f"   Capital: ₹{self.capital}")
        logger.info(f"   Max Position: ₹{CONFIG['MAX_POSITION_SIZE']}")
        logger.info(f"   Data: {CONFIG['DATA_SOURCE']}")
        logger.info(f"   Paper Trading: {self.paper_trading}")
    
    def send_telegram(self, message: str):
        """Send Telegram alert"""
        if not CONFIG.get('ENABLE_TELEGRAM_ALERTS', False):
            return
        
        token = CONFIG.get('TELEGRAM_BOT_TOKEN', '')
        chat_id = CONFIG.get('TELEGRAM_CHAT_ID', '')
        
        if not token or not chat_id:
            logger.warning("Telegram not configured - alerts disabled")
            return
        
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
            requests.post(url, data=data, timeout=10)
            logger.info(f"📱 Telegram alert sent")
        except Exception as e:
            logger.error(f"Failed to send Telegram: {e}")
    
    def is_market_open(self) -> bool:
        """Check if market is currently open (IST)"""
        import pytz
        
        # Get current time in IST
        ist = pytz.timezone('Asia/Kolkata')
        now_ist = datetime.now(ist)
        current_time = now_ist.time()
        
        market_start = time(CONFIG['MARKET_START_HOUR'], CONFIG['MARKET_START_MINUTE'])
        market_end = time(CONFIG['MARKET_END_HOUR'], CONFIG['MARKET_END_MINUTE'])
        
        # Check if weekend
        if now_ist.weekday() >= 5:  # Saturday = 5, Sunday = 6
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
        """Fetch stock data from yfinance - fast version"""
        try:
            import yfinance as yf
            import pandas as pd
            
            # Add .NS for NSE stocks
            ticker = f"{symbol}.NS" if CONFIG['MARKET'] == "NSE" else f"{symbol}.BS"
            
            # Use download for faster single-ticker fetch
            df = yf.download(ticker, period=period, interval=interval, progress=False, timeout=5)
            
            if df is None or df.empty:
                return None
            
            # Flatten multi-index columns
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]
            
            return df
        except Exception as e:
            logger.debug(f"Error fetching {symbol}: {e}")
            return None
    
    def calculate_indicators(self, df):
        """Calculate technical indicators"""
        if df is None or len(df) < 20:
            return None
        
        close = df['Close']
        
        # EMA 20
        df['EMA20'] = close.ewm(span=20).mean()
        
        # SMA for volume
        df['Volume_SMA20'] = df['Volume'].rolling(window=20).mean()
        
        # Current values
        current_price = close.iloc[-1]
        current_ema = df['EMA20'].iloc[-1]
        current_volume = df['Volume'].iloc[-1]
        avg_volume = df['Volume_SMA20'].iloc[-1]
        
        # Resistance (high of last 20 candles)
        resistance = df['High'].iloc[-20:].max()
        
        return {
            'price': current_price,
            'ema20': current_ema,
            'volume': current_volume,
            'avg_volume': avg_volume,
            'resistance': resistance,
            'volume_ratio': current_volume / avg_volume if avg_volume > 0 else 0
        }
    
    def check_entry_signal(self, symbol: str) -> dict:
        """Check if stock meets entry criteria"""
        df = self.get_stock_data(symbol)
        if df is None:
            return {'signal': False, 'reason': 'No data'}
        
        ind = self.calculate_indicators(df)
        if ind is None:
            return {'signal': False, 'reason': 'Insufficient data'}
        
        price = ind['price']
        
        # Entry conditions - AGGRESSIVE for cheap stocks
        conditions = []
        
        # For stocks under ₹100: Very loose criteria
        if price < 100:
            # Any positive signal is good
            if ind['volume_ratio'] >= 1.0:
                conditions.append(f"✅ Volume up: {ind['volume_ratio']:.1f}x")
            else:
                conditions.append(f"⚡ Low volume but trading: {ind['volume_ratio']:.1f}x")
            # Skip EMA check for cheap stocks
            conditions.append("💰 Cheap stock - relaxed rules")
        else:
            # Normal criteria for expensive stocks
            if ind['price'] > ind['ema20']:
                conditions.append("✅ Price > EMA20")
            else:
                return {'signal': False, 'reason': 'Price below EMA20'}
            
            if ind['volume_ratio'] >= CONFIG['MIN_VOLUME_MULTIPLIER']:
                conditions.append(f"✅ Volume spike: {ind['volume_ratio']:.1f}x")
            else:
                return {'signal': False, 'reason': f'No volume spike: {ind["volume_ratio"]:.1f}x'}
        
        # All conditions met
        return {
            'signal': True,
            'price': price,
            'volume_ratio': ind['volume_ratio'],
            'conditions': conditions
        }
        
        # All conditions met
        return {
            'signal': True,
            'price': ind['price'],
            'volume_ratio': ind['volume_ratio'],
            'conditions': conditions
        }
    
    def get_top_stocks(self) -> list:
        """Get list of affordable stocks (< ₹500) - verified working"""
        return [
            # Low price stocks (under ₹500) - all verified on yfinance
            'YESBANK', 'SUZLON', 'RPOWER', 'NHPC', 'HAL',
            'SJVN', 'NMDC', 'PNB', 'CANBK', 'UNIONBANK',
            'BANKBARODA', 'FEDERALBNK', 'RBLBANK', 'BANDHANBNK', 'ASHOKLEY',
            'TATAPOWER', 'TATASTEEL', 'SAIL', 'HINDALCO', 'ADANIENT',
            'NBCC', 'BEML', 'DEEPAKFERT', 'RCF', 'FACT',
            'GRAPHITE', 'BHEL', 'BEL', 'COALINDIA', 'IOC',
            'BPCL', 'GAIL', 'ONGC', 'NLCINDIA', 'IRB',
            # Mid price (₹500-₹1500)
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
        
        stocks = self.get_top_stocks()[:CONFIG['MAX_STOCKS_TO_SCAN']]
        
        for symbol in stocks:
            try:
                result = self.check_entry_signal(symbol)
                if result['signal']:
                    opportunities.append({
                        'symbol': symbol,
                        'price': result['price'],
                        'volume_ratio': result['volume_ratio'],
                        'conditions': result['conditions']
                    })
                    logger.info(f"   🎯 {symbol}: ₹{result['price']:.2f} (Vol: {result['volume_ratio']:.1f}x)")
                    
                    # Send Telegram alert
                    msg = f"🎯 <b>BUY SIGNAL</b>\n\nStock: {symbol}\nPrice: ₹{result['price']:.2f}\nVolume: {result['volume_ratio']:.1f}x\n\nConditions:\n" + "\n".join(result['conditions'])
                    self.send_telegram(msg)
                    
                    # Execute paper trade
                    if self.paper_trading and len(self.positions) < CONFIG['MAX_SIMULTANEOUS_TRADES']:
                        self.execute_paper_trade(symbol, result['price'])
            except Exception as e:
                logger.debug(f"{symbol}: {e}")
        
        return opportunities
    
    def log_trade(self, trade: dict):
        """Log trade to learning file"""
        trades_file = TRADES_DIR / "trades.md"
        
        entry = f"| {trade['date']} | {trade['symbol']} | ₹{trade['entry_price']} | ₹{trade['exit_price']} | {trade['pnl_pct']}% | {trade['reason']} |"
        
        with open(trades_file, 'a') as f:
            f.write(entry + '\n')
    
    def execute_paper_trade(self, symbol: str, price: float):
        """Execute a paper trade - AGGRESSIVE MODE"""
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        
        # Buy at least 1 share if possible
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
        
        position = {
            'symbol': symbol,
            'entry_price': price,
            'quantity': quantity,
            'entry_time': datetime.now(ist).strftime('%Y-%m-%d %H:%M'),
            'stop_loss': price * (1 - CONFIG['STOP_LOSS_PERCENT']/100),
            'take_profit': price * (1 + CONFIG['TAKE_PROFIT_PERCENT']/100)
        }
        
        self.positions.append(position)
        self.capital -= cost
        
        logger.info(f"📝 PAPER TRADE ENTERED: {symbol} | Qty: {quantity} | Entry: ₹{price:.2f} | Cost: ₹{cost:.2f}")
        
        # Send Telegram
        msg = f"📝 <b>BUY - PAPER TRADE</b>\n\nStock: {symbol}\nQty: {quantity}\nEntry: ₹{price:.2f}\nCost: ₹{cost:.2f}\nStop Loss: ₹{position['stop_loss']:.2f}\nTake Profit: ₹{position['take_profit']:.2f}\nCapital Left: ₹{self.capital:.2f}"
        self.send_telegram(msg)
        
        if quantity < 1:
            logger.warning(f"⚠️ Cannot buy {symbol} - price too high")
            return
        
        position = {
            'symbol': symbol,
            'entry_price': price,
            'quantity': quantity,
            'entry_time': datetime.now(ist).strftime('%Y-%m-%d %H:%M'),
            'stop_loss': price * (1 - CONFIG['STOP_LOSS_PERCENT']/100),
            'take_profit': price * (1 + CONFIG['TAKE_PROFIT_PERCENT']/100)
        }
        
        self.positions.append(position)
        self.capital -= position_size
        
        logger.info(f"📝 PAPER TRADE ENTERED: {symbol} | Qty: {quantity} | Entry: ₹{price:.2f}")
        
        # Send Telegram
        msg = f"📝 <b>PAPER TRADE ENTRY</b>\n\nStock: {symbol}\nQty: {quantity}\nEntry: ₹{price:.2f}\nStop Loss: ₹{position['stop_loss']:.2f}\nTake Profit: ₹{position['take_profit']:.2f}\nCapital Left: ₹{self.capital:.2f}"
        self.send_telegram(msg)
    
    def check_and_exit_positions(self):
        """Check positions for exit conditions"""
        if not self.positions:
            return
        
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        current_time = now.time()
        
        # Force exit at 3:20 PM
        force_exit_time = time(15, 20)
        
        positions_to_exit = []
        
        for i, pos in enumerate(self.positions):
            # Get current price
            df = self.get_stock_data(pos['symbol'])
            if df is None:
                continue
            
            current_price = df['Close'].iloc[-1]
            pos_pnl_pct = (current_price - pos['entry_price']) / pos['entry_price'] * 100
            
            # Check exit conditions
            exit_reason = None
            
            # Stop loss hit
            if current_price <= pos['stop_loss']:
                exit_reason = "STOP LOSS"
            # Take profit hit
            elif current_price >= pos['take_profit']:
                exit_reason = "TAKE PROFIT"
            # Force exit at 3:20 PM
            elif current_time >= force_exit_time:
                exit_reason = "FORCE EXIT (3:20 PM)"
            
            if exit_reason:
                # Calculate P&L
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
                
                # Telegram alert
                msg = f"📝 <b>PAPER TRADE EXIT</b>\n\nStock: {pos['symbol']}\nExit: ₹{current_price:.2f}\nP/L: ₹{pnl:.2f} ({pos_pnl_pct:.1f}%)\nReason: {exit_reason}\nCapital: ₹{self.capital:.2f}"
                self.send_telegram(msg)
                
                positions_to_exit.append(i)
        
        # Remove closed positions (in reverse to maintain index)
        for i in reversed(positions_to_exit):
            self.positions.pop(i)
        
        # Send daily summary if positions still open
        if self.positions:
            logger.info(f"📊 Open Positions: {len(self.positions)} | Capital: ₹{self.capital:.2f} | Daily P/L: ₹{self.daily_pnl:.2f}")
    
    def run(self):
        """Main bot loop"""
        logger.info("🤖 Bot starting...")
        
        while True:
            try:
                # Check ollama health (optional - bot works without it)
                # if not check_ollama_health():
                #     logger.warning("⚠️ Ollama not healthy, attempting restart...")
                #     restart_ollama()
                
                logger.info("=" * 50)
                if not self.is_market_open():
                    logger.info("🌙 Market closed. Sleeping...")
                    time_module.sleep(300)
                    continue
                
                # Scan for opportunities
                logger.info("Starting market scan...")
                opportunities = self.scan_market()
                logger.info(f"Scan complete. Found: {len(opportunities)}")
                
                # Check and exit positions
                self.check_and_exit_positions()
                
                if opportunities:
                    logger.info(f"Found {len(opportunities)} opportunities")
                
                # Sleep between scans
                time_module.sleep(CONFIG['SCAN_INTERVAL_MINUTES'] * 60)
                
            except KeyboardInterrupt:
                logger.info("🛑 Bot stopped by user")
                break
            except Exception as e:
                logger.error(f"Error: {e}")
                import traceback
                logger.error(traceback.format_exc())
                time_module.sleep(60)


if __name__ == "__main__":
    bot = TradingBot()
    bot.run()
