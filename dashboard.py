#!/usr/bin/env python3
"""
Trading Bot Dashboard - Web Interface
"""

from flask import Flask, jsonify, render_template_string
import json
import os
from pathlib import Path
from datetime import datetime

BOT_DIR = Path(__file__).parent
STATE_FILE = BOT_DIR / "state.json"

app = Flask(__name__)

HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>Trading Bot Dashboard</title>
    <meta http-equiv="refresh" content="30">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0d1117; color: #c9d1d9; margin: 0; padding: 20px; }
        .container { max-width: 800px; margin: 0 auto; }
        h1 { color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: 10px; }
        .card { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 20px; margin-bottom: 20px; }
        .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; }
        .stat { background: #21262d; padding: 15px; border-radius: 6px; text-align: center; }
        .stat-value { font-size: 24px; font-weight: bold; color: #58a6ff; }
        .stat-label { font-size: 12px; color: #8b949e; text-transform: uppercase; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #30363d; }
        th { color: #8b949e; font-weight: 500; font-size: 12px; text-transform: uppercase; }
        .symbol { color: #58a6ff; font-weight: bold; }
        .profit { color: #3fb950; }
        .loss { color: #f85149; }
        .updated { color: #8b949e; font-size: 12px; text-align: right; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📈 Trading Bot Dashboard</h1>
        
        <div class="card">
            <div class="stats">
                <div class="stat">
                    <div class="stat-value">₹{{ capital }}</div>
                    <div class="stat-label">Capital</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{{ positions|length }}</div>
                    <div class="stat-label">Open Positions</div>
                </div>
                <div class="stat">
                    <div class="stat-value {% if daily_pnl >= 0 %}profit{% else %}loss{% endif %}">₹{{ daily_pnl }}</div>
                    <div class="stat-label">Daily P/L</div>
                </div>
            </div>
        </div>
        
        <div class="card">
            <h2>Open Positions</h2>
            {% if positions %}
            <table>
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Entry</th>
                        <th>Entry</th>
                        <th>Current</th>
                        <th>P/L</th>
                        <th>Qty</th>
                        <th>Stop Loss</th>
                        <th>Take Profit</th>
                        <th>Entry Time</th>
                    </tr>
                </thead>
                <tbody>
                    {% for pos in positions %}
                    <tr>
                        <td class="symbol">{{ pos.symbol }}</td>
                        <td>₹{{ "%.2f"|format(pos.entry_price) }}</td>
                        <td>₹{{ "%.2f"|format(pos.current_price) }}</td>
                        <td class="{% if pos.pnl >= 0 %}profit{% else %}loss{% endif %}">₹{{ "%.2f"|format(pos.pnl) }} ({{ "%.1f"|format(pos.pnl_pct) }}%)</td>
                        <td>{{ pos.quantity }}</td>
                        <td>₹{{ "%.2f"|format(pos.stop_loss) }}</td>
                        <td>₹{{ "%.2f"|format(pos.take_profit) }}</td>
                        <td>{{ pos.entry_time }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <p style="color: #8b949e;">No open positions</p>
            {% endif %}
        </div>
        
        <div class="updated">Last updated: {{ updated }}</div>
    </div>
</body>
</html>
'''

@app.route('/')
def index():
    if not STATE_FILE.exists():
        return render_template_string(HTML, capital=0, positions=[], daily_pnl=0, updated="No data")
    
    with open(STATE_FILE) as f:
        state = json.load(f)
    
    # Fetch current prices
    positions = state.get('positions', [])
    for pos in positions:
        try:
            import yfinance as yf
            ticker = pos['symbol'] + '.NS'
            df = yf.download(ticker, period='1d', interval='5m', progress=False, timeout=5)
            if df is not None and not df.empty:
                current_price = df['Close'].iloc[-1]
                if hasattr(current_price, 'item'):
                    current_price = current_price.item()
                else:
                    current_price = float(current_price)
                pos['current_price'] = current_price
                pos['pnl'] = (current_price - pos['entry_price']) * pos['quantity']
                pos['pnl_pct'] = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
            else:
                pos['current_price'] = pos['entry_price']
                pos['pnl'] = 0
                pos['pnl_pct'] = 0
        except:
            pos['current_price'] = pos['entry_price']
            pos['pnl'] = 0
            pos['pnl_pct'] = 0
    
    return render_template_string(
        HTML,
        capital=f"{state.get('capital', 0):.2f}",
        positions=positions,
        daily_pnl=state.get('daily_pnl', 0),
        updated=state.get('saved_at', 'Unknown')
    )

@app.route('/api')
def api():
    if not STATE_FILE.exists():
        return jsonify({"error": "No state file"})
    
    with open(STATE_FILE) as f:
        state = json.load(f)
    
    return jsonify(state)

if __name__ == '__main__':
    print("🚀 Dashboard starting on http://0.0.0.0:5001")
    app.run(host='0.0.0.0', port=5001, debug=False)