# Twilio Monitor

Автоматическая проверка субаккаунтов, брендов и кампаний Twilio с записью в Google Sheets.

## Что делает

- Раз в месяц (1-го числа) автоматически запускается через GitHub Actions
- Можно запустить вручную в любой момент через вкладку **Actions → Run workflow**
- Проверяет все **субаккаунты** (основной аккаунт исключён)
- Для каждого субаккаунта показывает:
  - Статус **бренда** (Brand Registration)
  - Статус кампаний **MARKETING** и **ACCOUNT_NOTIFICATION**
- Записывает результат в Google Sheets (листы: `Brands`, `Campaigns`, `Run Log`)

---

## Настройка

### 1. Google Sheets — сервисный аккаунт

1. Открой [Google Cloud Console](https://console.cloud.google.com/)
2. Создай проект (или используй существующий)
3. Включи **Google Sheets API** и **Google Drive API**
4. Перейди в **IAM & Admin → Service Accounts → Create Service Account**
5. Назови его, нажми **Create and Continue → Done**
6. Открой созданный аккаунт → вкладка **Keys → Add Key → JSON**
7. Скачай JSON файл — это и есть `GOOGLE_CREDENTIALS_JSON`
8. Скопируй email сервисного аккаунта (вида `name@project.iam.gserviceaccount.com`)
9. Открой свою Google Таблицу → **Поделиться** → вставь этот email с правами **Редактор**
10. Скопируй ID таблицы из URL: `https://docs.google.com/spreadsheets/d/`**`ВОТ_ЭТОТ_ID`**`/edit`

### 2. GitHub Secrets

Перейди в репозиторий → **Settings → Secrets and variables → Actions → New repository secret**

Добавь 4 секрета:

| Название | Значение |
|---|---|
| `TWILIO_ACCOUNT_SID` | SID основного аккаунта Twilio (`ACxxx...`) |
| `TWILIO_AUTH_TOKEN` | Auth Token основного аккаунта |
| `GOOGLE_CREDENTIALS_JSON` | Содержимое скачанного JSON файла (вставь весь текст) |
| `GOOGLE_SHEET_ID` | ID Google Таблицы |

### 3. Загрузи файлы в GitHub

Структура репозитория:
```
.github/
  workflows/
    twilio-monitor.yml
check.py
README.md
```

---

## Ручной запуск

1. Открой репозиторий на GitHub
2. Перейди на вкладку **Actions**
3. Слева выбери **Twilio Monitor**
4. Нажми **Run workflow** → введи комментарий (необязательно) → **Run workflow**

---

## Структура Google Sheets

### Лист `Brands`
| Run Date | Subaccount Name | Subaccount SID | Subaccount Status | Brand Name | Brand SID | Brand Status | Failure Reason |

### Лист `Campaigns`
| Run Date | Subaccount Name | Subaccount SID | Service Name | Service SID | Campaign ID | Use Case | Campaign Status |

### Лист `Run Log`
История всех запусков — дата, количество субаккаунтов, брендов, строк.
