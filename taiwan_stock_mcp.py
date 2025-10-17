"""
台灣股票即時分析 MCP 服務器
Taiwan Stock Real-Time Analysis MCP Server

提供台灣股票市場的即時報價、技術分析、買賣建議等功能。
"""

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List, Dict, Any, Literal
from enum import Enum
import httpx
import json
from datetime import datetime
import asyncio

# 初始化 MCP 服務器
mcp = FastMCP("taiwan_stock_mcp")

# 常數定義
CHARACTER_LIMIT = 25000
TWSE_API_BASE = "https://mis.twse.com.tw/stock/api"
REQUEST_DELAY = 3.0  # 證交所限制：每5秒最多3個請求


# ==================== 回應格式 ====================

class ResponseFormat(str, Enum):
    """輸出格式選項"""
    MARKDOWN = "markdown"
    JSON = "json"


# ==================== 輸入模型 ====================

class StockQueryInput(BaseModel):
    """股票查詢輸入模型"""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra='forbid'
    )
    
    stock_code: str = Field(
        ..., 
        description="台灣股票代碼，例如：'2330'(台積電), '2317'(鴻海), '0050'(元大台灣50)",
        min_length=4,
        max_length=6,
        pattern=r'^[0-9]{4,6}$'
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="輸出格式：'markdown' 適合人類閱讀，'json' 適合程式處理"
    )
    
    @field_validator('stock_code')
    @classmethod
    def validate_stock_code(cls, v: str) -> str:
        """驗證股票代碼格式"""
        if not v.isdigit():
            raise ValueError("股票代碼必須是純數字")
        return v


class MultiStockQueryInput(BaseModel):
    """多支股票查詢輸入模型"""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra='forbid'
    )
    
    stock_codes: List[str] = Field(
        ...,
        description="股票代碼列表，例如：['2330', '2317', '0050']",
        min_items=1,
        max_items=20
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="輸出格式：'markdown' 或 'json'"
    )


class TechnicalAnalysisInput(BaseModel):
    """技術分析輸入模型"""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra='forbid'
    )
    
    stock_code: str = Field(
        ...,
        description="股票代碼，例如：'2330'",
        pattern=r'^[0-9]{4,6}$'
    )
    analysis_type: Literal["basic", "advanced", "full"] = Field(
        default="basic",
        description="分析類型：'basic'(基本分析), 'advanced'(進階分析), 'full'(完整分析)"
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="輸出格式"
    )


# ==================== 工具函數 ====================

async def fetch_stock_data(stock_codes: List[str], exchange: str = "tse") -> Dict[str, Any]:
    """
    從證交所 API 獲取股票即時資料
    
    Args:
        stock_codes: 股票代碼列表
        exchange: 交易所類型，'tse' 為上市，'otc' 為上櫃
    
    Returns:
        API 回應的 JSON 資料
    """
    # 構建股票代碼字串
    stock_list = '|'.join(f'{exchange}_{code}.tw' for code in stock_codes)
    url = f"{TWSE_API_BASE}/getStockInfo.jsp"
    params = {
        'ex_ch': stock_list,
        'json': '1',
        'delay': '0'
    }
    
    async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data.get('rtcode') != '0000':
                raise ValueError(f"API 回應錯誤: {data.get('rtmessage', 'Unknown error')}")
            
            return data
        except httpx.HTTPError as e:
            raise ValueError(f"無法連接證交所 API: {str(e)}")
        except json.JSONDecodeError:
            raise ValueError("API 回應格式錯誤")


def calculate_price_change(current: float, previous: float) -> tuple[float, str]:
    """
    計算價格變動
    
    Returns:
        (變動金額, 變動百分比字串)
    """
    change = current - previous
    change_percent = (change / previous * 100) if previous > 0 else 0
    sign = "+" if change >= 0 else ""
    return change, f"{sign}{change_percent:.2f}%"


def format_timestamp(timestamp: str) -> str:
    """格式化時間戳記"""
    try:
        ts = int(timestamp) / 1000
        dt = datetime.fromtimestamp(ts)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except:
        return timestamp


def analyze_technical_indicators(stock_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    技術指標分析

    分析當前價格與開盤、最高、最低、昨收的關係
    """
    try:
        # 安全地轉換數值，處理 '-' 和空值
        def safe_float(value, default=0.0):
            if value in ['', '-', None]:
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default

        def safe_int(value, default=0):
            if value in ['', '-', None]:
                return default
            try:
                return int(value)
            except (ValueError, TypeError):
                return default

        current = safe_float(stock_data.get('z'))  # 成交價
        open_price = safe_float(stock_data.get('o'))  # 開盤
        high = safe_float(stock_data.get('h'))  # 最高
        low = safe_float(stock_data.get('l'))  # 最低
        prev_close = safe_float(stock_data.get('y'))  # 昨收
        volume = safe_int(stock_data.get('v'))  # 成交量
        
        # 計算技術指標
        price_position = (current - low) / (high - low) * 100 if high > low else 50
        
        # 判斷趨勢
        if current > prev_close:
            trend = "上漲"
            trend_strength = "強勢" if current > open_price else "震盪上漲"
        elif current < prev_close:
            trend = "下跌"
            trend_strength = "弱勢" if current < open_price else "震盪下跌"
        else:
            trend = "平盤"
            trend_strength = "盤整"
        
        # 價格位置分析
        if price_position >= 80:
            position_desc = "高檔區（接近今日最高）"
        elif price_position >= 60:
            position_desc = "中高檔區"
        elif price_position >= 40:
            position_desc = "中檔區"
        elif price_position >= 20:
            position_desc = "中低檔區"
        else:
            position_desc = "低檔區（接近今日最低）"
        
        return {
            'trend': trend,
            'trend_strength': trend_strength,
            'price_position': price_position,
            'position_desc': position_desc,
            'volume': volume,
            'high': high,
            'low': low,
            'open': open_price,
            'current': current,
            'prev_close': prev_close
        }
    except Exception as e:
        return {'error': f"技術分析錯誤: {str(e)}"}


def generate_trading_suggestion(analysis: Dict[str, Any], stock_data: Dict[str, Any]) -> Dict[str, str]:
    """
    生成買賣建議
    
    基於技術分析結果提供交易建議
    """
    if 'error' in analysis:
        return {
            'action': '無法分析',
            'reason': analysis['error'],
            'risk_level': '未知'
        }
    
    current = analysis['current']
    prev_close = analysis['prev_close']
    price_position = analysis['price_position']
    trend = analysis['trend']
    
    # 買賣五檔分析
    best_bid = stock_data.get('b', '').split('_')[0] if stock_data.get('b') else ''
    best_ask = stock_data.get('a', '').split('_')[0] if stock_data.get('a') else ''
    
    try:
        bid_price = float(best_bid) if best_bid else 0
        ask_price = float(best_ask) if best_ask else 0
        spread = ask_price - bid_price if bid_price and ask_price else 0
    except:
        spread = 0
    
    # 決策邏輯
    if trend == "上漲":
        if price_position < 40:
            action = "買進"
            reason = f"股價處於{analysis['position_desc']}且呈上漲趨勢，具備向上動能"
            risk_level = "中等"
        elif price_position < 70:
            action = "觀望或小量買進"
            reason = f"股價已上漲至{analysis['position_desc']}，可等回檔再進場"
            risk_level = "中高"
        else:
            action = "觀望"
            reason = f"股價已在{analysis['position_desc']}，追高風險較大"
            risk_level = "高"
    
    elif trend == "下跌":
        if price_position > 60:
            action = "賣出或減碼"
            reason = f"股價雖處{analysis['position_desc']}但呈下跌趨勢，建議減碼"
            risk_level = "中高"
        elif price_position > 30:
            action = "觀望"
            reason = f"股價在{analysis['position_desc']}且下跌中，等待止跌訊號"
            risk_level = "中等"
        else:
            action = "觀望或小量買進"
            reason = f"股價已在{analysis['position_desc']}，可能接近短期支撐"
            risk_level = "中等"
    
    else:  # 平盤
        if price_position < 30:
            action = "可考慮買進"
            reason = f"股價在{analysis['position_desc']}，風險相對較低"
            risk_level = "中低"
        elif price_position > 70:
            action = "觀望"
            reason = f"股價在{analysis['position_desc']}，等待回檔"
            risk_level = "中等"
        else:
            action = "觀望"
            reason = "股價盤整中，等待明確趨勢"
            risk_level = "中等"
    
    return {
        'action': action,
        'reason': reason,
        'risk_level': risk_level,
        'spread': f"{spread:.2f}" if spread else "N/A"
    }


def format_stock_markdown(stock_data: Dict[str, Any], include_analysis: bool = False) -> str:
    """將股票資料格式化為 Markdown"""
    try:
        def safe_float(value, default=0.0):
            if value in ['', '-', None]:
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default

        code = stock_data.get('c', 'N/A')
        name = stock_data.get('n', 'N/A')
        current = safe_float(stock_data.get('z'))
        prev_close = safe_float(stock_data.get('y'))
        open_price = stock_data.get('o', '-')
        high = stock_data.get('h', '-')
        low = stock_data.get('l', '-')
        volume = stock_data.get('v', '-')
        time = format_timestamp(stock_data.get('tlong', '0'))

        # 判斷是否為盤後（成交價為0或為'-'）
        is_after_hours = (stock_data.get('z') in ['', '-', None] or current == 0)

        if is_after_hours:
            # 盤後時段，顯示昨收價
            price_display = f"{prev_close:.2f} (昨收，盤後)"
            change_display = "-"
        else:
            # 盤中時段，正常顯示
            change, change_pct = calculate_price_change(current, prev_close)
            price_display = f"{current:.2f} ({change:+.2f}, {change_pct})"
            change_display = f"{change:+.2f} ({change_pct})"

        # 買賣五檔
        bid_prices = stock_data.get('b', '').split('_')[:5]
        bid_volumes = stock_data.get('g', '').split('_')[:5]
        ask_prices = stock_data.get('a', '').split('_')[:5]
        ask_volumes = stock_data.get('f', '').split('_')[:5]

        status_note = "\n\n⏰ **盤後時段** - 以下為昨日收盤資訊，盤中時段將顯示即時報價。\n" if is_after_hours else ""

        markdown = f"""## 📊 {name} ({code}){status_note}

### {'收盤資訊' if is_after_hours else '即時報價'}
- **{'收盤價' if is_after_hours else '成交價'}**: {price_display}
- **開盤**: {open_price}
- **最高**: {high}
- **最低**: {low}
- **昨收**: {prev_close:.2f}
- **成交量**: {volume} 張
- **更新時間**: {time}

### 買賣五檔
"""
        
        # 買賣五檔表格
        markdown += "\n| 委買量 | 委買價 | 委賣價 | 委賣量 |\n"
        markdown += "|--------|--------|--------|--------|\n"
        
        for i in range(5):
            bid_vol = bid_volumes[i] if i < len(bid_volumes) else '-'
            bid_price = bid_prices[i] if i < len(bid_prices) else '-'
            ask_price = ask_prices[i] if i < len(ask_prices) else '-'
            ask_vol = ask_volumes[i] if i < len(ask_volumes) else '-'
            markdown += f"| {bid_vol} | {bid_price} | {ask_price} | {ask_vol} |\n"
        
        # 技術分析和建議
        if include_analysis:
            analysis = analyze_technical_indicators(stock_data)
            suggestion = generate_trading_suggestion(analysis, stock_data)
            
            markdown += f"""
### 📈 技術分析
- **趨勢**: {analysis.get('trend', 'N/A')} ({analysis.get('trend_strength', 'N/A')})
- **價格位置**: {analysis.get('position_desc', 'N/A')} ({analysis.get('price_position', 0):.1f}%)

### 💡 交易建議
- **建議動作**: {suggestion['action']}
- **理由**: {suggestion['reason']}
- **風險等級**: {suggestion['risk_level']}
- **買賣價差**: {suggestion['spread']}

---
⚠️ **免責聲明**: 以上分析僅供參考，不構成投資建議。投資有風險，請謹慎評估。
"""
        
        return markdown
        
    except Exception as e:
        return f"❌ 格式化股票資料時發生錯誤: {str(e)}"


# ==================== MCP 工具 ====================

@mcp.tool(name="get_stock_realtime_quote")
async def get_stock_realtime_quote(params: StockQueryInput) -> str:
    """取得台灣股票即時報價資訊。
    
    此工具從台灣證券交易所獲取指定股票的即時交易資料，包括成交價、漲跌幅、
    成交量、買賣五檔等資訊。適合快速查詢單一股票的當前狀態。
    
    Args:
        params (StockQueryInput): 包含以下欄位:
            - stock_code (str): 股票代碼 (4-6位數字)
            - response_format (str): 輸出格式 ('markdown' 或 'json')
    
    Returns:
        str: 格式化的股票即時報價資訊
        
    Examples:
        查詢台積電(2330)即時報價:
        - stock_code: "2330"
        - response_format: "markdown"
    
    Note:
        - 資料來源為台灣證券交易所，約每5秒更新一次
        - 請遵守 API 使用限制，避免過於頻繁請求
    """
    try:
        # 嘗試上市股票
        data = await fetch_stock_data([params.stock_code], "tse")
        
        if not data.get('msgArray'):
            # 嘗試上櫃股票
            data = await fetch_stock_data([params.stock_code], "otc")
        
        if not data.get('msgArray'):
            return f"❌ 找不到股票代碼 {params.stock_code} 的資料，請確認代碼是否正確"
        
        stock_info = data['msgArray'][0]
        
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(stock_info, ensure_ascii=False, indent=2)
        else:
            return format_stock_markdown(stock_info, include_analysis=False)
            
    except Exception as e:
        return f"❌ 查詢失敗: {str(e)}"


@mcp.tool(name="get_multiple_stocks_quotes")
async def get_multiple_stocks_quotes(params: MultiStockQueryInput) -> str:
    """同時取得多支台灣股票的即時報價資訊。
    
    此工具可一次查詢多支股票的即時交易資料，適合用於投資組合監控、
    類股比較等場景。
    
    Args:
        params (MultiStockQueryInput): 包含以下欄位:
            - stock_codes (List[str]): 股票代碼列表，最多20支
            - response_format (str): 輸出格式
    
    Returns:
        str: 所有股票的即時報價資訊
        
    Examples:
        查詢台積電、鴻海、聯發科:
        - stock_codes: ["2330", "2317", "2454"]
        - response_format: "markdown"
    
    Note:
        - 建議一次查詢不超過10支股票以提升效能
        - 會自動識別上市/上櫃股票
    """
    try:
        results = []
        
        # 分批處理股票代碼（上市和上櫃分開）
        for code in params.stock_codes:
            try:
                data = await fetch_stock_data([code], "tse")
                
                if not data.get('msgArray'):
                    data = await fetch_stock_data([code], "otc")
                
                if data.get('msgArray'):
                    results.append(data['msgArray'][0])
                else:
                    results.append({'c': code, 'error': '查無資料'})
                
                # 避免請求過於頻繁
                await asyncio.sleep(0.5)
                
            except Exception as e:
                results.append({'c': code, 'error': str(e)})
        
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(results, ensure_ascii=False, indent=2)
        else:
            markdown = "# 📊 多支股票即時報價\n\n"
            for stock in results:
                if 'error' in stock:
                    markdown += f"## ❌ {stock['c']}: {stock['error']}\n\n"
                else:
                    markdown += format_stock_markdown(stock, include_analysis=False)
                    markdown += "\n---\n\n"
            
            return markdown
            
    except Exception as e:
        return f"❌ 查詢失敗: {str(e)}"


@mcp.tool(name="analyze_stock_with_suggestion")
async def analyze_stock_with_suggestion(params: TechnicalAnalysisInput) -> str:
    """進行股票技術分析並提供買賣建議。
    
    此工具結合即時報價資料進行技術面分析，包括趨勢判斷、價格位置分析，
    並基於分析結果提供交易建議。適合需要交易決策參考的場景。
    
    Args:
        params (TechnicalAnalysisInput): 包含以下欄位:
            - stock_code (str): 股票代碼
            - analysis_type (str): 分析類型 ('basic', 'advanced', 'full')
            - response_format (str): 輸出格式
    
    Returns:
        str: 包含技術分析和交易建議的完整報告
        
    Analysis Types:
        - basic: 基本趨勢和建議
        - advanced: 包含更多技術指標
        - full: 完整分析報告
        
    Examples:
        分析台積電並取得買賣建議:
        - stock_code: "2330"
        - analysis_type: "full"
        - response_format: "markdown"
    
    Warning:
        分析結果僅供參考，不構成實際投資建議。
        投資前請務必進行完整評估並考慮個人風險承受能力。
    """
    try:
        # 獲取即時資料
        data = await fetch_stock_data([params.stock_code], "tse")
        
        if not data.get('msgArray'):
            data = await fetch_stock_data([params.stock_code], "otc")
        
        if not data.get('msgArray'):
            return f"❌ 找不到股票代碼 {params.stock_code} 的資料"
        
        stock_info = data['msgArray'][0]
        
        # 進行技術分析
        analysis = analyze_technical_indicators(stock_info)
        suggestion = generate_trading_suggestion(analysis, stock_info)
        
        if params.response_format == ResponseFormat.JSON:
            result = {
                'stock_info': stock_info,
                'technical_analysis': analysis,
                'trading_suggestion': suggestion
            }
            return json.dumps(result, ensure_ascii=False, indent=2)
        else:
            # 使用增強版的 Markdown 格式
            return format_stock_markdown(stock_info, include_analysis=True)
            
    except Exception as e:
        return f"❌ 分析失敗: {str(e)}"


@mcp.tool(name="compare_stocks")
async def compare_stocks(params: MultiStockQueryInput) -> str:
    """比較多支股票的表現和技術面。
    
    此工具同時分析多支股票，並以表格形式比較各股票的關鍵指標，
    幫助投資人快速了解不同標的的相對強弱。
    
    Args:
        params (MultiStockQueryInput): 包含股票代碼列表和輸出格式
    
    Returns:
        str: 股票比較分析結果
        
    Examples:
        比較台積電、聯電、力積電:
        - stock_codes: ["2330", "2303", "6770"]
        - response_format: "markdown"
    """
    try:
        comparisons = []
        
        for code in params.stock_codes:
            try:
                data = await fetch_stock_data([code], "tse")
                if not data.get('msgArray'):
                    data = await fetch_stock_data([code], "otc")
                
                if data.get('msgArray'):
                    stock_info = data['msgArray'][0]
                    analysis = analyze_technical_indicators(stock_info)
                    suggestion = generate_trading_suggestion(analysis, stock_info)
                    
                    comparisons.append({
                        'code': code,
                        'name': stock_info.get('n', 'N/A'),
                        'price': float(stock_info.get('z', 0)),
                        'change': calculate_price_change(
                            float(stock_info.get('z', 0)),
                            float(stock_info.get('y', 0))
                        )[1],
                        'trend': analysis.get('trend', 'N/A'),
                        'position': f"{analysis.get('price_position', 0):.1f}%",
                        'suggestion': suggestion['action'],
                        'risk': suggestion['risk_level']
                    })
                
                await asyncio.sleep(0.5)
                
            except Exception as e:
                comparisons.append({
                    'code': code,
                    'error': str(e)
                })
        
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(comparisons, ensure_ascii=False, indent=2)
        else:
            markdown = "# 📊 股票比較分析\n\n"
            markdown += "| 代碼 | 名稱 | 現價 | 漲跌幅 | 趨勢 | 價格位置 | 建議 | 風險 |\n"
            markdown += "|------|------|------|--------|------|----------|------|------|\n"
            
            for comp in comparisons:
                if 'error' in comp:
                    markdown += f"| {comp['code']} | - | - | - | ❌ {comp['error']} | - | - | - |\n"
                else:
                    markdown += f"| {comp['code']} | {comp['name']} | {comp['price']:.2f} | {comp['change']} | {comp['trend']} | {comp['position']} | {comp['suggestion']} | {comp['risk']} |\n"
            
            markdown += "\n---\n⚠️ **免責聲明**: 以上分析僅供參考，不構成投資建議。"
            
            return markdown
            
    except Exception as e:
        return f"❌ 比較分析失敗: {str(e)}"


# ==================== 主程式 ====================

if __name__ == "__main__":
    # 啟動 MCP 服務器
    mcp.run()
