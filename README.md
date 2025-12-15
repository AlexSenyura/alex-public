# Ютуба Дивисі🔥

Streamlit-застосунок для пошуку і аналітики відео YouTube з метриками "віральності", snapshot у SQLite, кластеризацією заголовків та AI-генератором ключів і тем.

## Запуск
1. Встановити залежності: `pip install -r requirements.txt`
2. Запустити застосунок: `streamlit run app.py`

ENV змінні (опційно):
- `YT_VIRAL_DB_PATH` — шлях до SQLite (default `yt_viral.db`)
- `OPENAI_MODEL` — модель OpenAI (default `gpt-5-mini`)
- `OPENAI_API_KEY` — ключ для AI функцій (якщо немає — AI буде вимкнено)
