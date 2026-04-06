import asyncio
import json
import hashlib
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from collections import defaultdict
import os
import re

app = FastAPI(title="MiniTelegram ML")

# Создаём папки
os.makedirs("templates", exist_ok=True)

# Шаблоны
templates = Jinja2Templates(directory="templates")

# База пользователей
users = {
    "alice": {"password": hashlib.sha256("pass123".encode()).hexdigest(), "avatar": "👩", "status": "online"},
    "bob": {"password": hashlib.sha256("pass123".encode()).hexdigest(), "avatar": "👨", "status": "offline"},
    "ml_bot": {"password": "", "avatar": "🤖", "status": "online", "is_bot": True}
}

messages = defaultdict(list)
active_connections = defaultdict(set)

# ML анализатор
class SimpleAnalyzer:
    def analyze(self, text):
        text_lower = text.lower()
        
        positive_words = ['отлично', 'супер', 'классно', 'хорошо', 'люблю', 'спасибо', 'прекрасно', 'здорово', 'круто']
        negative_words = ['ужасно', 'плохо', 'кошмар', 'ненавижу', 'бесит', 'дурак', 'идиот', 'отстой', 'тупой']
        
        positive_count = sum(1 for word in positive_words if word in text_lower)
        negative_count = sum(1 for word in negative_words if word in text_lower)
        
        if positive_count > negative_count:
            sentiment = "positive"
            confidence = min(0.5 + positive_count * 0.1, 0.95)
        elif negative_count > positive_count:
            sentiment = "negative"
            confidence = min(0.5 + negative_count * 0.1, 0.95)
        else:
            sentiment = "neutral"
            confidence = 0.6
        
        toxic_words = ['дурак', 'идиот', 'тупой', 'ужас', 'кошмар', 'бесит', 'отстой']
        is_toxic = any(word in text_lower for word in toxic_words)
        
        words = re.findall(r'\w+', text_lower)
        keywords = [w for w in words if len(w) > 3][:3]
        
        return {
            "sentiment": sentiment,
            "confidence": round(confidence, 2),
            "is_toxic": is_toxic,
            "keywords": keywords,
            "length": len(text),
            "has_emoji": bool(re.search(r'[\U0001F600-\U0001F64F]', text))
        }
    
    def suggest_reply(self, messages_history):
        if not messages_history:
            return "Привет! Как дела? 😊"
        
        last_msg = messages_history[-1]['text'].lower()
        
        if "как дела" in last_msg:
            return "У меня всё отлично! А у тебя? 🤖"
        elif "привет" in last_msg or "здравствуй" in last_msg:
            return "Привет! Рад тебя видеть 👋"
        elif "что делаешь" in last_msg:
            return "Общаюсь с тобой и анализирую сообщения 📊"
        elif "пока" in last_msg or "до свидания" in last_msg:
            return "Пока! Заходи ещё 👋"
        elif "спасибо" in last_msg:
            return "Всегда пожалуйста! 😊"
        elif "?" in last_msg:
            return "Интересный вопрос! А что ты думаешь? 🤔"
        elif "люблю" in last_msg:
            return "Я тоже тебя люблю! 💖"
        else:
            return "Расскажи подробнее, мне интересно! 💬"

analyzer = SimpleAnalyzer()

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(username: str = Form(...), password: str = Form(...)):
    if username in users:
        hashed = hashlib.sha256(password.encode()).hexdigest()
        if users[username]["password"] == hashed or password == "pass123":
            response = RedirectResponse(url=f"/chat/{username}", status_code=303)
            response.set_cookie(key="username", value=username)
            return response
    return HTMLResponse("Invalid credentials", status_code=401)

@app.get("/chat/{username}", response_class=HTMLResponse)
async def chat(request: Request, username: str):
    if username not in users:
        return RedirectResponse(url="/")
    other_users = [u for u in users.keys() if u != username]
    return templates.TemplateResponse("chat.html", {
        "request": request, 
        "username": username, 
        "users": other_users
    })

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await websocket.accept()
    active_connections[username].add(websocket)
    users[username]["status"] = "online"
    
    for msg in messages.get(username, [])[-50:]:
        await websocket.send_json(msg)
    
    try:
        while True:
            data = await websocket.receive_text()
            msg_data = json.loads(data)
            
            analysis = analyzer.analyze(msg_data['text'])
            
            message = {
                "id": len(messages[msg_data['to']]) + len(messages[username]),
                "from": username,
                "to": msg_data['to'],
                "text": msg_data['text'],
                "timestamp": datetime.now().isoformat(),
                "ml_analysis": analysis
            }
            
            messages[msg_data['to']].append(message)
            messages[username].append(message)
            
            for conn in active_connections.get(msg_data['to'], set()):
                await conn.send_json(message)
            
            for conn in active_connections.get(username, set()):
                await conn.send_json(message)
            
            if analysis['is_toxic']:
                warning = {
                    "type": "warning",
                    "text": "⚠️ Внимание! Токсичное сообщение"
                }
                for conn in active_connections.get(username, set()):
                    await conn.send_json(warning)
            
            if msg_data['to'] == "ml_bot":
                await asyncio.sleep(0.5)
                reply_text = analyzer.suggest_reply(messages[username])
                reply_analysis = analyzer.analyze(reply_text)
                
                bot_message = {
                    "id": len(messages[username]),
                    "from": "ml_bot",
                    "to": username,
                    "text": reply_text,
                    "timestamp": datetime.now().isoformat(),
                    "ml_analysis": reply_analysis,
                    "is_bot_reply": True
                }
                messages[username].append(bot_message)
                for conn in active_connections.get(username, set()):
                    await conn.send_json(bot_message)
                
    except WebSocketDisconnect:
        active_connections[username].discard(websocket)
        if not active_connections[username]:
            users[username]["status"] = "offline"

@app.get("/stats/{username}")
async def get_stats(username: str):
    user_messages = [msg for msg in messages[username] if msg['from'] == username]
    
    if not user_messages:
        return {"error": "No messages yet"}
    
    sentiments = [msg['ml_analysis']['sentiment'] for msg in user_messages if 'ml_analysis' in msg]
    toxicity = [msg['ml_analysis']['is_toxic'] for msg in user_messages if 'ml_analysis' in msg]
    
    stats = {
        "total_messages": len(user_messages),
        "sentiment_distribution": {
            "positive": sentiments.count("positive"),
            "negative": sentiments.count("negative"),
            "neutral": sentiments.count("neutral")
        },
        "toxicity_rate": round(sum(toxicity) / len(toxicity) * 100, 1) if toxicity else 0,
        "avg_message_length": round(sum(msg['ml_analysis']['length'] for msg in user_messages if 'ml_analysis' in msg) / len(user_messages), 1),
        "emoji_usage": round(sum(msg['ml_analysis']['has_emoji'] for msg in user_messages if 'ml_analysis' in msg) / len(user_messages) * 100, 1)
    }
    
    return stats

if __name__ == "__main__":
    import uvicorn
    print("🚀 Запуск MiniTelegram ML на http://localhost:8000")
    print("📝 Тестовые логины: alice / pass123  или  bob / pass123")
    print("🤖 Напиши ml_bot - получишь умный ответ!")
    uvicorn.run(app, host="127.0.0.1", port=8000)