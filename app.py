import telebot
import time
from telebot import types
from telebot.apihelper import ApiTelegramException
import json
import os
import logging
from datetime import datetime, timedelta
from flask import Flask, Response
import threading
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# --- КОНСТАНТЫ И ХРАНИЛИЩА ---
# Хранение корзин и заказов
CARTS = {}
ORDERS = {}         # order_id → { "user_id": ..., "items": [...], "status": "new", ... }
NEXT_ORDER_ID = 1
ORDERS_FILE = "orders.json"

# Хранение тикетов поддержки
SUPPORT_TICKETS = {}
NEXT_TICKET_ID = 1
SUPPORT_COOLDOWN_SECONDS = 300  # 5 минут

# ID группы для уведомлений о заказах
ADMIN_GROUP_ID = os.getenv("ADMIN_GROUP_ID", "-4975322862")
SUPPORT_GROUP_ID = os.getenv("SUPPORT_GROUP_ID", "-5095562342")

# ID администраторов (добавьте свой ID)
ADMIN_IDS = [1144206940, 6539363874] 

# Хранилище состояний пользователей
user_data = {}
last_bot_msg = {}


REFERRALS_FILE = "referrals.json"
REFERRALS = {}  # user_id -> {"invited_by": ID, "balance": 0, "invited_count": 0}

def save_referrals():
    try:
        with open(REFERRALS_FILE, 'w', encoding='utf-8') as f:
            json.dump(REFERRALS, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Error saving referrals: {e}")

def load_referrals():
    global REFERRALS
    if os.path.exists(REFERRALS_FILE):
        try:
            with open(REFERRALS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Конвертируем ключи в int
                REFERRALS = {int(k): v for k, v in data.items()}
        except Exception as e:
            logger.error(f"Error loading referrals: {e}")
            REFERRALS = {}
    else:
        save_referrals()

def delete_user_msg_delayed(chat_id, message_id, delay=5):
    """Фоновое удаление сообщения через X секунд"""
    def _delete():
        time.sleep(delay)
        try:
            bot.delete_message(chat_id, message_id)
        except Exception:
            pass
    threading.Thread(target=_delete, daemon=True).start()


PROMOCODES_FILE = "promocodes.json"
PROMOCODES = {}  # "CODE": {"type": "percent"|"fixed", "value": 10, "left": 100}

def save_promocodes():
    try:
        with open(PROMOCODES_FILE, 'w', encoding='utf-8') as f:
            json.dump(PROMOCODES, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Error saving promocodes: {e}")

def load_promocodes():
    global PROMOCODES
    if os.path.exists(PROMOCODES_FILE):
        try:
            with open(PROMOCODES_FILE, 'r', encoding='utf-8') as f:
                PROMOCODES = json.load(f)
        except Exception as e:
             logger.error(f"Error loading promocodes: {e}")
             PROMOCODES = {}
    else:
        save_promocodes()


TICKETS_FILE = "tickets.json"

def save_tickets():
    try:
        with open(TICKETS_FILE, 'w', encoding='utf-8') as f:
            json.dump(SUPPORT_TICKETS, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Error saving tickets: {e}")

def load_tickets():
    global SUPPORT_TICKETS, NEXT_TICKET_ID
    if os.path.exists(TICKETS_FILE):
        try:
            with open(TICKETS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Конвертируем ключи обратно в int
                SUPPORT_TICKETS = {int(k): v for k, v in data.items()}
                if SUPPORT_TICKETS:
                    NEXT_TICKET_ID = max(SUPPORT_TICKETS.keys()) + 1
        except Exception as e:
            logger.error(f"Error loading tickets: {e}")
            SUPPORT_TICKETS = {}


PRODUCTS_FILE = "products.json"
PRODUCTS = {"welcome": None} # Категории будут добавляться сюда динамически

FAQ_ANSWERS = [
    "1. Выберите товар и размер\n2. Нажмите «➕ В корзину» или «🛒 Заказать»\n3. Перейдите в корзину и нажмите «📦 Оформить заказ»\n4. Ожидайте сообщения от менеджера в течение 15 минут",
    "Оплата производится **100% предоплатой**:\n• Перевод на СБП (Систему быстрых платежей)\n• QR-код\n\nПосле оплаты мы отправляем товар в тот же день.",
    "г. Новосибирск, ул. Крылова, д. 1\n\nСамовывоз возможен по предварительной договорённости.",
    "Возврат возможен **в течение 14 дней**, если:\n• Товар не был в носке\n• Сохранены ярлыки и упаковка\n\nОбратитесь к менеджеру через бота.",
]

_product_cache = {}


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Вставьте ваш токен бота
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN env variable is missing!")

bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=20)

# --- УТИЛИТЫ ---

def is_admin(user_id):
    return user_id in ADMIN_IDS

def get_user_info(user):
    username = f"@{user.username}" if user.username else "Нет username"
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Нет имени"
    return username, full_name


def save_products():
    try:
        with open(PRODUCTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(PRODUCTS, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Error saving products: {e}")

def load_products():
    global PRODUCTS
    if os.path.exists(PRODUCTS_FILE):
        with open(PRODUCTS_FILE, 'r', encoding='utf-8') as f:
            try:
                PRODUCTS = json.load(f)
            except:
                PRODUCTS = {"welcome": None}
        if "welcome" not in PRODUCTS:
            PRODUCTS["welcome"] = None
    else:
        PRODUCTS = {"welcome": None}
        save_products()

def save_orders():
    global NEXT_ORDER_ID
    data = {"next_order_id": NEXT_ORDER_ID, "orders": ORDERS}
    try:
        with open(ORDERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Error saving orders: {e}")

def load_orders():
    global NEXT_ORDER_ID, ORDERS
    if os.path.exists(ORDERS_FILE):
        try:
            with open(ORDERS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                NEXT_ORDER_ID = data.get("next_order_id", 1)
                ORDERS = data.get("orders", {})
                # Конвертируем ключи обратно в int, если они были строками
                ORDERS = {int(k): v for k, v in ORDERS.items()}
        except Exception as e:
             logger.error(f"Error loading orders: {e}")
             ORDERS = {}
             NEXT_ORDER_ID = 1
    else:
        save_orders()

def find_product_by_id(product_id):
    for cat in PRODUCTS:
        if cat == "welcome" or not isinstance(PRODUCTS[cat], dict): continue
        for subcat in PRODUCTS[cat]:
            for p in PRODUCTS[cat][subcat]:
                if p.get('id') == product_id:
                    return p
    return None

def get_next_product_id():
    max_id = 0
    for cat in PRODUCTS:
        if cat == "welcome" or not isinstance(PRODUCTS[cat], dict):
            continue
        for subcat in PRODUCTS[cat]:
            for p in PRODUCTS[cat][subcat]:
                if p.get('id', 0) > max_id:
                    max_id = p['id']
    return max_id + 1

# --- МЕНЮ ---

def get_reply_main_menu():
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    # Создаем кнопки для всех категорий, которые есть в PRODUCTS (кроме welcome)
    buttons = []
    for cat_name in PRODUCTS.keys():
        if cat_name != "welcome":
            buttons.append(types.KeyboardButton(cat_name))
    
    mk.add(*buttons)
    mk.add(types.KeyboardButton("🛒 Корзина"), types.KeyboardButton("🎫 Промокод"))
    mk.add(types.KeyboardButton("👥 Рефералы"), types.KeyboardButton("🆘 Поддержка"))
    return mk


@bot.message_handler(func=lambda message: message.chat.id == int(SUPPORT_GROUP_ID) and message.reply_to_message)
def admin_reply_via_telegram_handler(message):
    """Админ просто отвечает на сообщение бота в группе — ответ летит юзеру."""
    replied_msg_id = message.reply_to_message.message_id
    
    # Ищем, к какому тикету относится это сообщение
    ticket = None
    for t_id, t_data in SUPPORT_TICKETS.items():
        if t_data.get('admin_msg_id') == replied_msg_id:
            ticket = t_data
            break
            
    if ticket:
        try:
            # Отправляем сообщение пользователю
            client_text = f"👨‍💻 *Ответ поддержки по вашему запросу #{ticket['id']}:*\n\n{message.text}"
            bot.send_message(ticket['user_id'], client_text, parse_mode="Markdown")
            
            # Обновляем историю в тикете
            ticket['history'].append(f"👨‍💻 Менеджер: {message.text}")
            ticket['status'] = 'in_work'
            save_tickets()
            
            # Подтверждаем админу (реакцией или коротким сообщением)
            bot.reply_to(message, f"✅ Отправлено пользователю #{ticket['id']}")
        except Exception as e:
            bot.reply_to(message, "❌ Не удалось отправить. Возможно, юзер заблокировал бота.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("support_history::"))
def support_history_callback(call):
    ticket_id = int(call.data.split("::")[1])
    ticket = SUPPORT_TICKETS.get(ticket_id)
    
    if ticket:
        history_text = "\n\n".join(ticket['history'])
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, f"📜 *История тикета #{ticket_id}:*\n\n{history_text}", parse_mode="Markdown")
    else:
        bot.answer_callback_query(call.id, "Тикет не найден.")


@bot.message_handler(func=lambda message: message.text == "🎫 Промокод")
def promo_button_handler(message):
    user_id = message.from_user.id
    if user_id not in user_data:
        user_data[user_id] = {}
    
    user_data[user_id]['waiting_for'] = 'input_promo'
    bot.send_message(message.chat.id, "⌨️ Введите ваш промокод:")

def get_admin_reply_menu():
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True)
    mk.add("➕ Создать", "🗑 Удалить")
    mk.add("🖼 Приветствие", "📊 Статистика")
    mk.add("🚚 Заказы", "🎫 Промокоды")
    mk.add("◀️ Главное меню")
    return mk

def get_admin_create_menu():
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True)
    mk.add("📁 Категорию", "📂 Подкатегорию", "🎁 Товар", "◀️ Назад")
    return mk

def get_admin_delete_menu_root():
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True)
    # Используем стандартные названия
    mk.add("❌ Удалить Категорию", "❌ Удалить Подкатегорию", "❌ Удалить Товар")
    mk.add("◀️ Назад")
    return mk


def get_admin_add_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("➕ Обувь", callback_data="admin_add_shoes"),
        types.InlineKeyboardButton("➕ Одежда", callback_data="admin_add_clothes")
    )
    markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="admin_panel"))
    return markup




@bot.callback_query_handler(func=lambda c: c.data == "admin_promo_add")
def admin_promo_add_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id): return
    
    # Инициализируем данные пользователя, если их нет
    if user_id not in user_data:
        user_data[user_id] = {}
        
    user_data[user_id]['waiting_for'] = 'add_promo_name'
    bot.send_message(call.message.chat.id, "📝 Введите название для нового промокода (напр: SALE2025):")
    bot.answer_callback_query(call.id)



def get_admin_category_menu():
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    # Показываем все существующие категории
    cats = [c for c in PRODUCTS.keys() if c != "welcome"]
    for i in range(0, len(cats), 2):
        mk.add(*cats[i:i+2])
    mk.add("◀️ Назад")
    return mk

def get_admin_edit_products_reply_menu(category: str):
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    for product in PRODUCTS.get(category, []):
        if not isinstance(product, dict):
            continue
        name = product.get('name', '').strip()
        if name == "◀️ Назад":
            continue  # ❗ Исключаем фейковый товар
        # Убери проверку наличия
        if 'stock' not in product:
            continue
        mk.add(f"{name} - {product['price']} ₽")
    mk.add("◀️ Назад")
    return mk

@bot.message_handler(func=lambda message: message.text == "◀️ Назад" and user_data.get(message.from_user.id, {}).get('waiting_for') == 'delete_product')
def admin_delete_back_handler(message):
    """Обработчик кнопки Назад при удалении товара"""
    if not is_admin(message.from_user.id):
        return
    user_id = message.from_user.id
    if user_id in user_data:
        user_data[user_id]['waiting_for'] = None
    bot.send_message(
        message.chat.id,
        "🔧 Админ-панель",
        reply_markup=get_admin_reply_menu()
    )



@bot.message_handler(func=lambda message: message.text == "✏️ Редактировать")
def admin_edit_select_reply(message):
    if not is_admin(message.from_user.id):
        return
    user_data[message.from_user.id] = {'waiting_for': 'edit_category'}  # Устанавливаем состояние
    bot.send_message(
        message.chat.id,
        "Выберите категорию:",
        reply_markup=get_admin_category_menu()
    )




@bot.message_handler(func=lambda message: message.text == "👟 Обувь")
def admin_edit_shoes_reply(message):
    if not is_admin(message.from_user.id):
        return
    bot.send_message(
        message.chat.id,
        "Выберите товар для редактирования:",
        reply_markup=get_admin_edit_products_reply_menu("shoes")
    )

@bot.message_handler(func=lambda message: 
    " - " in message.text and 
    "₽" in message.text and 
    user_data.get(message.from_user.id, {}).get('waiting_for') != 'delete_product_by_name')
def admin_edit_product_by_name(message):
    if not is_admin(message.from_user.id):
        return

    text = message.text
    name = text.split(" - ")[0].strip()

    product = None
    for category in ["shoes", "clothes"]:
        for p in PRODUCTS.get(category, []):
            if isinstance(p, dict) and p.get('name') == name:
                product = p
                break
        if product:
            break

    if not product:
        bot.send_message(message.chat.id, "❌ Товар не найден.")
        return

    product_id = product['id']
    user_data[message.from_user.id] = {
        'waiting_for': 'edit_product',
        'editing_product_id': product_id
    }

    bot.send_message(
        message.chat.id,
        f"✏️ Что вы хотите изменить для *{product['name']}*?",
        parse_mode="Markdown",
        reply_markup=get_admin_product_actions_reply_menu(product_id)
    )


def get_admin_product_actions_reply_menu(product_id):
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    mk.add("📝 Название", "💰 Цену")
    mk.add("📏 Размеры", "🖼 Фото")
    mk.add("📦 Наличие", "◀️ Назад")
    return mk






@bot.message_handler(func=lambda message: message.text == "💰 Цену")
def admin_change_price_reply(message):
    if not is_admin(message.from_user.id):
        return
    user_id = message.from_user.id
    product_id = user_data[user_id].get('editing_product_id')
    if not product_id:
        bot.send_message(message.chat.id, "❌ Ошибка: не выбран товар.")
        return
    user_data[user_id]['waiting_for'] = 'price_edit'
    bot.send_message(message.chat.id, "Введите новую цену товара (только число):")

@bot.message_handler(func=lambda message: message.text == "📏 Размеры")
def admin_change_sizes_reply(message):
    if not is_admin(message.from_user.id):
        return
    user_id = message.from_user.id
    product_id = user_data[user_id].get('editing_product_id')
    if not product_id:
        bot.send_message(message.chat.id, "❌ Ошибка: не выбран товар.")
        return
    user_data[user_id]['waiting_for'] = 'sizes_edit'
    bot.send_message(message.chat.id, "Введите новые размеры через запятую (например: 36, 37, 38):")

@bot.message_handler(func=lambda message: message.text == "🖼 Фото")
def admin_change_photo_reply(message):
    if not is_admin(message.from_user.id):
        return
    user_id = message.from_user.id
    product_id = user_data[user_id].get('editing_product_id')
    if not product_id:
        bot.send_message(message.chat.id, "❌ Ошибка: не выбран товар.")
        return
    user_data[user_id]['waiting_for'] = f'photo_edit_{product_id}'
    bot.send_message(message.chat.id, "Отправьте новое фото товара:")

@bot.message_handler(func=lambda message: message.text == "📦 Наличие")
def admin_change_stock_reply(message):
    if not is_admin(message.from_user.id):
        return
    user_id = message.from_user.id
    if user_id not in user_data:
        user_data[user_id] = {}
    product_id = user_data[user_id].get('editing_product_id')
    if not product_id:
        bot.send_message(message.chat.id, "❌ Ошибка: не выбран товар.")
        return

    product = find_product_by_id(product_id)
    if not product or 'stock' not in product:
        bot.send_message(message.chat.id, "❌ Ошибка: нет информации о наличии.")
        return

    markup = types.InlineKeyboardMarkup(row_width=3)
    for size, available in product['stock'].items():
        status = "✅" if available else "❌"
        new_val = 0 if available else 1
        markup.add(types.InlineKeyboardButton(
            f"{status} {size}",
            callback_data=f"toggle_stock_{product_id}_{size}_{new_val}"
        ))
    markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data=f"admin_edit_prod_{product_id}"))

    bot.send_message(
        message.chat.id,
        f"📦 *Наличие: {product['name']}*\n\nНажмите на размер для переключения:",
        reply_markup=markup,
        parse_mode="Markdown"
    )





@bot.callback_query_handler(func=lambda c: c.data.startswith("toggle_stock_"))
def toggle_stock_handler(call):
    if not is_admin(call.from_user.id):
        return
    try:
        parts = call.data.split("_")
        product_id = int(parts[2])
        size = parts[3]
        new_status = bool(int(parts[4]))

        # Ищем товар во всех категориях
        product = find_product_by_id(product_id)
        if product and 'stock' in product and size in product['stock']:
            product['stock'][size] = new_status
            save_products()
            bot.answer_callback_query(call.id, f"{size}: {'в наличии' if new_status else 'нет в наличии'}")

            # Обновляем меню наличия
            product = find_product_by_id(product_id)
            if product:
                markup = types.InlineKeyboardMarkup(row_width=3)
                for s, available in product['stock'].items():
                    status = "✅" if available else "❌"
                    new_val = 0 if available else 1
                    markup.add(types.InlineKeyboardButton(
                        f"{status} {s}",
                        callback_data=f"toggle_stock_{product_id}_{s}_{new_val}"
                    ))
                markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data=f"admin_edit_prod_{product_id}"))

                try:
                    bot.edit_message_reply_markup(
                        call.message.chat.id,
                        call.message.message_id,
                        reply_markup=markup
                    )
                except Exception as e:
                    logger.error(f"Ошибка обновления меню наличия: {e}")
            return
        bot.answer_callback_query(call.id, "Ошибка: товар или размер не найден")
    except Exception as e:
        logger.error(f"Ошибка переключения наличия: {e}")
        bot.answer_callback_query(call.id, "Ошибка")






@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_edit_prod_"))
def admin_edit_prod_callback(call):
    if not is_admin(call.from_user.id):
        return
    try:
        product_id = int(call.data.split("_")[3])
        product = find_product_by_id(product_id)
        if not product:
            bot.answer_callback_query(call.id, "Товар не найден")
            return

        bot.edit_message_text(
            f"✏️ Что вы хотите изменить для *{product['name']}*?",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=get_admin_product_actions_reply_menu(product_id)
        )
    except Exception as e:
        logger.error(f"Ошибка в admin_edit_prod_callback: {e}")
        bot.answer_callback_query(call.id, "Ошибка")




@bot.message_handler(func=lambda message: user_data.get(message.from_user.id, {}).get('waiting_for') == 'price_edit')
def admin_edit_price_handler(message):
    if not is_admin(message.from_user.id):
        return

    try:
        new_price = int(message.text.strip())
        if new_price <= 0:
            bot.send_message(message.chat.id, "❌ Ошибка: цена должна быть больше 0.")
            return
    except ValueError:
        bot.send_message(message.chat.id, "❌ Ошибка: введите корректную цену (только число).")
        return

    user_id = message.from_user.id
    product_id = user_data[user_id].get('editing_product_id')
    if not product_id:
        bot.send_message(message.chat.id, "❌ Ошибка: не выбран товар.")
        return

    updated = False
    # Ищем товар во всех категориях
    for cat in PRODUCTS:
        if cat == "welcome" or not isinstance(PRODUCTS[cat], dict):
            continue
        for subcat in PRODUCTS[cat]:
            for product in PRODUCTS[cat][subcat]:
                if isinstance(product, dict) and product.get('id') == product_id:
                    product['price'] = new_price
                    updated = True
                    break
            if updated:
                break
        if updated:
            break
    
    if updated:
        save_products()
        bot.send_message(message.chat.id, "✅ Цена обновлена!", reply_markup=get_admin_reply_menu())
        user_data[user_id]['waiting_for'] = None
        user_data[user_id].pop('editing_product_id', None)
    else:
        bot.send_message(message.chat.id, "❌ Товар не найден.")
        user_data[user_id]['waiting_for'] = None


@bot.message_handler(func=lambda message: user_data.get(message.from_user.id, {}).get('waiting_for') == 'sizes_edit')
def admin_edit_sizes_handler(message):
    if not is_admin(message.from_user.id):
        return

    sizes = [s.strip() for s in message.text.split(',') if s.strip()]
    if not sizes:
        bot.send_message(message.chat.id, "❌ Размеры не могут быть пустыми. Введите размеры через запятую:")
        return
    
    # Валидация: проверяем, что размеры не пустые строки
    sizes = [s for s in sizes if s]
    if not sizes:
        bot.send_message(message.chat.id, "❌ Размеры не могут быть пустыми. Введите корректные размеры через запятую:")
        return

    user_id = message.from_user.id
    product_id = user_data[user_id].get('editing_product_id')
    if not product_id:
        bot.send_message(message.chat.id, "❌ Ошибка: не выбран товар.")
        return

    updated = False
    # Ищем товар во всех категориях
    for cat in PRODUCTS:
        if cat == "welcome" or not isinstance(PRODUCTS[cat], dict):
            continue
        for subcat in PRODUCTS[cat]:
            for product in PRODUCTS[cat][subcat]:
                if isinstance(product, dict) and product.get('id') == product_id:
                    product['sizes'] = sizes
                    # Обновляем stock: сохраняем существующие статусы, добавляем новые как True
                    old_stock = product.get('stock', {})
                    product['stock'] = {size: old_stock.get(size, True) for size in sizes}
                    updated = True
                    break
            if updated:
                break
        if updated:
            break
    
    if updated:
        save_products()
        bot.send_message(message.chat.id, "✅ Размеры обновлены!", reply_markup=get_admin_reply_menu())
        user_data[user_id]['waiting_for'] = None
        user_data[user_id].pop('editing_product_id', None)
    else:
        bot.send_message(message.chat.id, "❌ Товар не найден.")
        user_data[user_id]['waiting_for'] = None








@bot.message_handler(func=lambda message: message.text == "📝 Название")
def admin_change_name_reply(message):
    if not is_admin(message.from_user.id):
        return
    user_id = message.from_user.id
    # --- ПРОВЕРЯЕМ, ЧТО ЕСТЬ product_id ---
    product_id = user_data[user_id].get('editing_product_id')
    if not product_id:
        bot.send_message(message.chat.id, "❌ Ошибка: не выбран товар.")
        return
    user_data[user_id]['waiting_for'] = 'name_edit'
    bot.send_message(message.chat.id, "Введите новое название товара:")




@bot.message_handler(func=lambda message: user_data.get(message.from_user.id, {}).get('waiting_for') == 'name_edit')
def admin_edit_name_handler(message):
    if not is_admin(message.from_user.id):
        return

    new_name = message.text.strip()
    if not new_name:
        bot.send_message(message.chat.id, "❌ Название не может быть пустым. Введите новое название:")
        return
    
    user_id = message.from_user.id
    product_id = user_data[user_id].get('editing_product_id')
    if not product_id:
        bot.send_message(message.chat.id, "❌ Ошибка: не выбран товар.")
        return

    updated = False
    # Ищем товар во всех категориях
    for cat in PRODUCTS:
        if cat == "welcome" or not isinstance(PRODUCTS[cat], dict):
            continue
        for subcat in PRODUCTS[cat]:
            for product in PRODUCTS[cat][subcat]:
                if isinstance(product, dict) and product.get('id') == product_id:
                    product['name'] = new_name
                    updated = True
                    break
            if updated:
                break
        if updated:
            break
    
    if updated:
        save_products()
        bot.send_message(message.chat.id, "✅ Название обновлено!", reply_markup=get_admin_reply_menu())
        user_data[user_id]['waiting_for'] = None
        user_data[user_id].pop('editing_product_id', None)
    else:
        bot.send_message(message.chat.id, "❌ Товар не найден.")
        user_data[user_id]['waiting_for'] = None

@bot.callback_query_handler(func=lambda c: c.data.startswith("order_delete_"))
def admin_order_delete(call):
    if not is_admin(call.from_user.id):
        return
    
    try:
        order_id = int(call.data.split("_")[2])
        if order_id in ORDERS:
            del ORDERS[order_id]
            save_orders()
            bot.answer_callback_query(call.id, "Заказ удалён!")
            # Возвращаемся в список
            admin_orders_list(call)
        else:
            bot.answer_callback_query(call.id, "Заказ не найден")
    except Exception as e:
        bot.answer_callback_query(call.id, "Ошибка удаления")

def get_orders_list_menu():
    markup = types.InlineKeyboardMarkup(row_width=1)
   
    sorted_orders = sorted(
        ORDERS.items(),
        key=lambda x: (x[1]['status'] != 'new', x[1]['created_at']),
        reverse=True
    )
   
    if not sorted_orders:
        markup.add(types.InlineKeyboardButton("Нет заказов", callback_data="noop"))
    else:
        for order_id, order in sorted_orders:
            status_emoji = {
                'new': '🆕',
                'in_processing': '🔄',
                'sent': '🚚',
                'completed': '✅'
            }.get(order['status'], '❓')
           
            items_count = len(order['items'])
            if items_count == 1:
                item_info = f"{order['items'][0]['name']} ({order['items'][0]['size']})"
            else:
                item_info = f"{items_count} товаров"
           
            client_name = order['full_name'][:15] + "..." if len(order['full_name']) > 18 else order['full_name']
            item_info_short = item_info[:25] + "..." if len(item_info) > 28 else item_info
           
            button_text = f"{status_emoji} #{order_id} | {item_info_short} | {client_name}"
           
            markup.add(types.InlineKeyboardButton(
                button_text,
                callback_data=f"order_view_{order_id}"
            ))
   
    markup.add(types.InlineKeyboardButton("◀️ Назад в админ-панель", callback_data="admin_panel"))
    return markup


def get_admin_edit_menu(category):
    """Получает меню редактирования товаров для категории (работает с новой структурой с подкатегориями)"""
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    # Проверяем структуру категории
    cat_data = PRODUCTS.get(category, {})
    if not isinstance(cat_data, dict):
        # Старая структура (список товаров) - для обратной совместимости
        if isinstance(cat_data, list):
            for product in cat_data:
                if isinstance(product, dict) and 'id' in product:
                    markup.add(types.InlineKeyboardButton(
                        f"{product['name']} - {product['price']} ₽",
                        callback_data=f"admin_edit_prod_{product['id']}"
                    ))
    else:
        # Новая структура (словарь подкатегорий)
        for subcat in cat_data:
            for product in cat_data[subcat]:
                if isinstance(product, dict) and 'id' in product:
                    markup.add(types.InlineKeyboardButton(
                        f"{product['name']} - {product['price']} ₽",
                        callback_data=f"admin_edit_prod_{product['id']}"
                    ))
    
    markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="admin_panel"))
    return markup

def get_admin_product_actions(product_id):
    product = find_product_by_id(product_id)
    if not product:
        return types.InlineKeyboardMarkup()
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📝 Изменить название", callback_data=f"admin_change_name_{product_id}"),
        types.InlineKeyboardButton("💰 Изменить цену", callback_data=f"admin_change_price_{product_id}")
    )
    markup.add(
        types.InlineKeyboardButton("📏 Изменить размеры", callback_data=f"admin_change_sizes_{product_id}"),
        types.InlineKeyboardButton("🖼 Изменить фото", callback_data=f"admin_change_photo_{product_id}")
    )
    markup.add(
        types.InlineKeyboardButton("📦 Наличие размеров", callback_data=f"admin_stock_{product_id}")
    )
    markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="admin_edit_select"))
    return markup

def get_admin_delete_menu():
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    # Только категории с товарами
    for category in ["shoes", "clothes"]:
        for product in PRODUCTS.get(category, []):
            if isinstance(product, dict) and 'id' in product:
                markup.add(types.InlineKeyboardButton(
                    f"❌ {product['name']} - {product['price']} ₽",
                    callback_data=f"admin_del_prod_{product['id']}"
                ))
    
    if len(markup.keyboard) == 0:
        markup.add(types.InlineKeyboardButton("Нет товаров для удаления", callback_data="admin_panel"))
    else:
        markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="admin_panel"))
    return markup

def get_cart_menu():
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(types.InlineKeyboardButton("📦 Оформить заказ", callback_data="cart_checkout"))
    mk.add(types.InlineKeyboardButton("🧹 Очистить корзину", callback_data="cart_clear"))
    mk.add(types.InlineKeyboardButton("◀️ Главное меню", callback_data="back_main"))
    return mk

def get_faq_menu():
    mk = types.InlineKeyboardMarkup(row_width=1)
    mk.add(types.InlineKeyboardButton("1. Как сделать заказ?", callback_data="faq_0"))
    mk.add(types.InlineKeyboardButton("2. Способы оплаты?", callback_data="faq_1"))
    mk.add(types.InlineKeyboardButton("3. Самовывоз?", callback_data="faq_2"))
    mk.add(types.InlineKeyboardButton("4. Возврат?", callback_data="faq_3"))
    mk.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_main"))
    return mk

def get_support_admin_menu(ticket_id, status):
    mk = types.InlineKeyboardMarkup(row_width=2)
    
    if status == "new":
        mk.add(types.InlineKeyboardButton("🛠 Взять в работу", callback_data=f"support_take::{ticket_id}"))
    elif status == "in_work":
        mk.add(types.InlineKeyboardButton("✉️ Ответить", callback_data=f"support_reply::{ticket_id}"))
        mk.add(types.InlineKeyboardButton("✅ Закрыть тикет", callback_data=f"support_close::{ticket_id}"))
    
    return mk

# --- ФУНКЦИИ КАТАЛОГА ---

def size_menu(category: str) -> types.InlineKeyboardMarkup:
    available_sizes = set()
    for p in PRODUCTS.get(category, []):
        if not isinstance(p, dict):
            continue
        name = p.get('name', '').strip()
        if name == "◀️ Назад":
            continue
        stock = p.get("stock", {})
        for size in stock.keys():
            available_sizes.add(size)
    if not available_sizes:
        return None

    def sort_key(s):
        s = s.strip()
        if s.isdigit():
            return (0, int(s))
        order = {"S": 1, "M": 2, "L": 3, "XL": 4, "XXL": 5}
        return (1, order.get(s.upper(), 999))

    sizes = sorted(available_sizes, key=sort_key)
    mk = types.InlineKeyboardMarkup(row_width=4)
    row = []
    for s in sizes:
        row.append(types.InlineKeyboardButton(s, callback_data=f"select_size_{category}_{s}"))
        if len(row) == 4:
            mk.add(*row)
            row = []
    if row:
        mk.add(*row)
    mk.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_main"))
    return mk

def show_browse(call, category: str, size: str, idx: int):
    """Показывает конкретный товар с проверкой наличия и навигацией."""
    # Фильтруем товары выбранной категории, у которых есть этот размер
    filtered = [p for p in PRODUCTS.get(category, []) if size in p.get("sizes", [])]
    
    if not filtered:
        bot.answer_callback_query(call.id, f"❌ В категории {category} пока нет товаров этого размера.")
        return

    # Проверка границ индекса (чтобы не выйти за пределы списка)
    if idx < 0: idx = 0
    if idx >= len(filtered): idx = len(filtered) - 1
        
    product = filtered[idx]
    
    # Проверка наличия конкретного размера
    in_stock = product.get('stock', {}).get(size, False)
    stock_text = "✅ В наличии" if in_stock else "❌ Нет в наличии"

    # --- ИСПРАВЛЕННЫЙ ТЕКСТ (ДИНАМИЧЕСКИЙ) ---
    caption = (
        f"📦 *{category}* | Размер: {size}\n\n"
        f"*{product['name']}*\n"
        f"💰 Цена: {product['price']} ₽\n"
        f"📊 Статус: {stock_text}"
    )

    mk = types.InlineKeyboardMarkup(row_width=3)
    
    # --- 1 РЯД: НАВИГАЦИЯ ---
    nav_btns = []
    # Кнопка "Назад" (влево)
    if idx > 0:
        nav_btns.append(types.InlineKeyboardButton("◀️", callback_data=f"browse_{category}_{size}_{idx - 1}"))
    else:
        nav_btns.append(types.InlineKeyboardButton(" ", callback_data="noop"))
    
    # Счётчик страниц
    nav_btns.append(types.InlineKeyboardButton(f"{idx + 1} / {len(filtered)}", callback_data="noop"))
    
    # Кнопка "Вперед" (вправо)
    if idx < len(filtered) - 1:
        nav_btns.append(types.InlineKeyboardButton("▶️", callback_data=f"browse_{category}_{size}_{idx + 1}"))
    else:
        nav_btns.append(types.InlineKeyboardButton(" ", callback_data="noop"))
    
    mk.add(*nav_btns)

    # --- 2 РЯД: ДЕЙСТВИЯ (ЗАКАЗ / КОРЗИНА) ---
    if in_stock:
        mk.add(
            types.InlineKeyboardButton("🛒 Заказать", callback_data=f"order_{product['id']}_{size}"),
            types.InlineKeyboardButton("➕ В корзину", callback_data=f"cart_add::{product['id']}::{size}")
        )
    else:
        mk.add(types.InlineKeyboardButton("🚫 Нет в наличии", callback_data="noop"))

    # --- 3 РЯД: ВОЗВРАТ ---
    # Кнопка возврата к выбору размеров именно этой категории
    mk.add(types.InlineKeyboardButton("↩️ К выбору размеров", callback_data=f"cat_{category}"))
    mk.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="back_main"))

    # Отправляем фото и удаляем предыдущее сообщение
    send_one_photo(
        call.message.chat.id,
        product["image"],
        caption=caption,
        reply_markup=mk,
        user_id=call.from_user.id
    )

# --- СООБЩЕНИЯ ---

def send_one_msg(chat_id, text, reply_markup=None, parse_mode="Markdown", user_id=None):
    if user_id and last_bot_msg.get(user_id):
        try:
            bot.edit_message_text(text, chat_id, last_bot_msg[user_id], reply_markup=reply_markup, parse_mode=parse_mode)
            return last_bot_msg[user_id]
        except:
            try: bot.delete_message(chat_id, last_bot_msg[user_id])
            except: pass
    mid = bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup).message_id
    if user_id: last_bot_msg[user_id] = mid
    return mid


def send_one_photo(chat_id, photo, caption, reply_markup=None, parse_mode="Markdown", user_id=None):
    """Для фото: удаляет старое и шлет новое (фото нельзя превратить в текст редактированием)."""
    if user_id and last_bot_msg.get(user_id):
        try: bot.delete_message(chat_id, last_bot_msg[user_id])
        except: pass
    
    mid = bot.send_photo(chat_id, photo, caption=caption, parse_mode=parse_mode,
                         reply_markup=reply_markup).message_id
    if user_id:
        last_bot_msg[user_id] = mid
    return mid


def send_welcome(chat_id, user_id):
    """Главное меню: медиа-файл или текст, но всегда 1 сообщение."""
    welcome = PRODUCTS.get("welcome")
    if welcome and welcome.get("file_id"):
        kwargs = dict(chat_id=chat_id, caption=welcome["caption"],
                      parse_mode="Markdown", reply_markup=get_reply_main_menu())
        if user_id and last_bot_msg.get(user_id):
            try:
                bot.delete_message(chat_id, last_bot_msg[user_id])
            except:
                pass
        if welcome["type"] == "photo":
            mid = bot.send_photo(photo=welcome["file_id"], **kwargs).message_id
        elif welcome["type"] == "video":
            mid = bot.send_video(video=welcome["file_id"], **kwargs).message_id
        elif welcome["type"] == "animation":
            mid = bot.send_animation(animation=welcome["file_id"], **kwargs).message_id
        else:
            mid = send_one_msg(
                chat_id,
                "🏪 *Добро пожаловать в Orphelins Dorés!*\n\nВыберите категорию:",
                parse_mode="Markdown",
                reply_markup=get_reply_main_menu(),
                user_id=user_id
            )
    else:
        mid = send_one_msg(
    chat_id,
    "🏪 *Добро пожаловать в Orphelins Dorés!*\n\nВыберите категорию:",
    parse_mode="Markdown",
    reply_markup=get_reply_main_menu(),
    user_id=user_id
)
    if user_id:
        last_bot_msg[user_id] = mid
    return mid

# --- ОБРАБОТЧИКИ REPLY-КНОПОК ---
@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_stock_"))
def admin_edit_stock(call):
    if not is_admin(call.from_user.id):
        return
    product_id = int(call.data.split("_")[2])
    product = find_product_by_id(product_id)
    if not product or 'stock' not in product:
        bot.answer_callback_query(call.id, "Нет размеров")
        return

    markup = types.InlineKeyboardMarkup(row_width=3)
    for size, available in product['stock'].items():
        status = "✅" if available else "❌"
        new_val = 0 if available else 1
        markup.add(types.InlineKeyboardButton(
            f"{status} {size}",
            callback_data=f"toggle_stock_{product_id}_{size}_{new_val}"
        ))
    markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data=f"admin_edit_prod_{product_id}"))

    bot.edit_message_text(
        f"📦 *Наличие: {product['name']}*\n\nНажмите на размер для переключения:",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )


@bot.message_handler(commands=['start'])
def send_welcome_command(message):
    delete_user_msg_delayed(message.chat.id, message.message_id)
    user_id = message.from_user.id
    
    # Инициализируем пользователя в системе рефералов, если его нет
    if user_id not in REFERRALS:
        REFERRALS[user_id] = {"invited_by": None, "balance": 0, "invited_count": 0}
        
        # Проверяем, есть ли в команде /start аргумент (ID пригласившего)
        args = message.text.split()
        if len(args) > 1:
            referrer_id = args[1]
            try:
                referrer_id = int(referrer_id)
                # Проверяем, что пригласивший существует и это не сам пользователь
                if referrer_id in REFERRALS and referrer_id != user_id:
                    REFERRALS[user_id]["invited_by"] = referrer_id
                    REFERRALS[referrer_id]["invited_count"] += 1
                    REFERRALS[referrer_id]["balance"] += 500  # Например, 500 бонусов за друга
                    
                    # Уведомляем пригласившего
                    try:
                        bot.send_message(referrer_id, f"🎉 По вашей ссылке зарегистрировался новый пользователь! Вам начислено 500 бонусов.")
                    except:
                        pass
            except ValueError:
                pass
        save_referrals()

    send_welcome(message.chat.id, user_id)

@bot.message_handler(func=lambda message: message.text == "👟 Смотреть обувь")
def show_shoes_reply(message):
    delete_user_msg_delayed(message.chat.id, message.message_id, delay=1)
    logger.info("[DEBUG] 👟 Смотреть обувь")
    mk = size_menu("shoes")
    if mk:
        send_one_msg(message.chat.id, "👟 Выберите размер обуви:",
             reply_markup=mk, user_id=message.from_user.id)
    else:
        send_one_msg(message.chat.id, "Товары скоро появятся!",
                     reply_markup=get_reply_main_menu(), user_id=message.from_user.id)
        
@bot.message_handler(func=lambda message: message.text == "👕 Смотреть одежду")
def show_clothes_reply(message):
    delete_user_msg_delayed(message.chat.id, message.message_id, delay=1)
    logger.info("[DEBUG] 👕 Смотреть одежду")
    mk = size_menu("clothes")
    if mk:
        send_one_msg(message.chat.id, "👕 Выберите размер одежды:",
             reply_markup=mk, user_id=message.from_user.id)
    else:
        send_one_msg(message.chat.id, "Товары скоро появятся!",
                     reply_markup=get_reply_main_menu(), user_id=message.from_user.id)


@bot.message_handler(func=lambda message: message.text == "🛒 Корзина")
def show_cart(message):
    user_id = message.from_user.id
    cart = CARTS.get(user_id, [])
    
    if not cart:
        send_one_msg(message.chat.id, "🛒 Ваша корзина пуста", reply_markup=get_reply_main_menu(), user_id=user_id)
        return

    total = sum(item["price"] for item in cart)
    
    # Проверка промокода
    applied_promo = user_data.get(user_id, {}).get('applied_promo')
    discount = 0
    promo_text = ""
    
    if applied_promo and applied_promo in PROMOCODES:
        p_data = PROMOCODES[applied_promo]
        if p_data['type'] == 'percent':
            discount = (total * p_data['value']) // 100
        else:
            discount = p_data['value']
        promo_text = f"🎫 Промокод {applied_promo}: -{discount} ₽\n"

    final_total = max(0, total - discount)

    text = f"🛒 *Ваша корзина:*\n\n"
    for item in cart:
        text += f"• {item['name']} ({item['size']}) — {item['price']} ₽\n"
    
    text += f"\n💰 Сумма: {total} ₽\n{promo_text}🔥 *Итого: {final_total} ₽*"

    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(types.InlineKeyboardButton("📦 Оформить заказ", callback_data="cart_checkout"))
    mk.add(types.InlineKeyboardButton("🎫 Ввести промокод", callback_data="cart_apply_promo"))
    mk.add(types.InlineKeyboardButton("🧹 Очистить", callback_data="cart_clear"), 
           types.InlineKeyboardButton("🏠 Меню", callback_data="back_main"))
    
    send_one_msg(message.chat.id, text, parse_mode="Markdown", reply_markup=mk, user_id=user_id)

@bot.message_handler(func=lambda message: message.text == "❓ FAQ")
def faq_reply(message):
    logger.info("[DEBUG] ❓ FAQ")
    send_one_msg(
        message.chat.id,
        "❓ *Часто задаваемые вопросы*",
        parse_mode="Markdown",
        reply_markup=get_faq_menu(),
        user_id=message.from_user.id
    )



@bot.callback_query_handler(func=lambda c: c.data == "cart_apply_promo")
def promo_prompt(call):
    
    user_id = call.from_user.id
    if user_id not in user_data:
        user_data[user_id] = {}

    user_data[user_id]['waiting_for'] = 'input_promo'
    bot.send_message(call.message.chat.id, "⌨️ Введите промокод:")

@bot.callback_query_handler(func=lambda c: c.data == "cart_apply_points")
def points_apply(call):
    user_id = call.from_user.id
    balance = REFERRALS.get(user_id, {}).get('balance', 0)
    
    if balance <= 0:
        bot.answer_callback_query(call.id, "У вас нет баллов")
        return

    # Записываем в сессию, что пользователь хочет списать баллы
    if user_id not in user_data:
        user_data[user_id] = {}
    user_data[user_id]['applied_bonuses'] = balance
    bot.answer_callback_query(call.id, f"✅ Баллы применены")
    
    # Обновляем корзину через создание фейкового message объекта
    class FakeMessage:
        def __init__(self, chat_id, user_id):
            self.chat = type('obj', (object,), {'id': chat_id})()
            self.from_user = type('obj', (object,), {'id': user_id})()
    
    fake_msg = FakeMessage(call.message.chat.id, user_id)
    show_cart(fake_msg)

# Обработка текстового ввода промокода (добавь в handle_text)
# Внутри handle_text добавь условие:
# if waiting_for == 'input_promo':
#    code = text.upper()
#    if code in PROMOCODES and PROMOCODES[code]['left'] > 0:
#        user_data[user_id]['applied_promo'] = code
#        bot.send_message(message.chat.id, "✅ Промокод применен!")
#    else:
#        bot.send_message(message.chat.id, "❌ Неверный или истекший промокод.")
#    del user_data[user_id]['waiting_for']
#    show_cart(message)



@bot.message_handler(func=lambda message: message.text == "🆘 Поддержка")
def support_reply(message):
    delete_user_msg_delayed(message.chat.id, message.message_id, delay=1)
    user_id = message.from_user.id
    
    # 1. Проверка кулдауна
    if user_id in user_data and 'support_cooldown_until' in user_data[user_id]:
        cooldown_until = user_data[user_id]['support_cooldown_until']
        if datetime.now() < cooldown_until:
            remaining = cooldown_until - datetime.now()
            minutes = int(remaining.total_seconds() // 60)
            seconds = int(remaining.total_seconds() % 60)
            text = f"⏳ *Подождите!* Вы сможете создать новый тикет через {minutes} мин. {seconds} сек."
            send_one_msg(message.chat.id, text, parse_mode="Markdown", user_id=user_id)
            return

    # 2. Проверка активного тикета
    active_ticket = next((t for t in SUPPORT_TICKETS.values() if t['user_id'] == user_id and t['status'] != 'closed'), None)
    if active_ticket:
        text = f"⚠️ У вас уже есть активный тикет *#{active_ticket['id']}* со статусом: *{active_ticket['status']}*.\nДождитесь ответа или закрытия тикета."
        send_one_msg(message.chat.id, text, parse_mode="Markdown", user_id=user_id)
        return

    # 3. Начало создания тикета
    if user_id not in user_data:
        user_data[user_id] = {}
    user_data[user_id]['waiting_for'] = 'support_message'
    
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("❌ Отмена", callback_data="admin_cancel"))
    
    send_one_msg(
        message.chat.id,
        "✍️ *Опишите вашу проблему или вопрос.*",
        parse_mode="Markdown",
        reply_markup=mk,
        user_id=user_id
    )


@bot.callback_query_handler(func=lambda c: c.data == "cart_checkout")
def cart_checkout_handler(call):
    user_id = call.from_user.id
    cart = CARTS.get(user_id, [])
   
    if not cart:
        bot.answer_callback_query(call.id, "❌ Ваша корзина пуста!")
        return
    unavailable_items = []
    for item in cart:
        prod = find_product_by_id(item['product_id'])
        # Проверяем, есть ли такой товар и есть ли этот размер в наличии
        if not prod or not prod.get('stock', {}).get(item['size'], False):
            unavailable_items.append(f"• {item['name']} ({item['size']})")

    if unavailable_items:
        error_text = "⚠️ К сожалению, следующие товары уже раскупили:\n\n" + "\n".join(unavailable_items) + "\n\nПожалуйста, очистите корзину и выберите другие товары."
        bot.send_message(call.message.chat.id, error_text)
        bot.answer_callback_query(call.id, "Ошибка: товаров нет в наличии")
        return # БЛОКИРУЕМ ЗАКАЗ
    
    # --- 1. РАСЧЕТ БАЗОВОЙ СУММЫ ---
    total_price = sum(item['price'] for item in cart)
    
    # Получаем примененные скидки из памяти
    if user_id not in user_data:
        user_data[user_id] = {}
        
    applied_promo = user_data[user_id].get('applied_promo')
    applied_bonuses = user_data[user_id].get('applied_bonuses', 0)
    
    discount_amount = 0

    # --- 2. ПРОВЕРКА И СПИСАНИЕ ПРОМОКОДА ---
    if applied_promo:
        if applied_promo in PROMOCODES:
            p = PROMOCODES[applied_promo]
            
            # Если код закончился, пока юзер думал
            if p['left'] <= 0:
                bot.send_message(call.message.chat.id, f"⚠️ Промокод `{applied_promo}` больше не активен (лимит исчерпан).")
                user_data[user_id]['applied_promo'] = None
                return # Прерываем оформление, чтобы юзер видел актуальную цену
            
            # Считаем сумму скидки
            if p['type'] == 'percent':
                discount_amount = (total_price * p['value']) // 100
            else:
                discount_amount = p['value']
            
            # УМЕНЬШАЕМ ЛИМИТ И СОХРАНЯЕМ
            PROMOCODES[applied_promo]['left'] -= 1
            save_promocodes()
        else:
            # Если промокод удалили из базы
            user_data[user_id]['applied_promo'] = None
            applied_promo = None

    # --- 3. ПРОВЕРКА И СПИСАНИЕ БАЛЛОВ ---
    final_bonuses = 0
    if applied_bonuses > 0:
        user_balance = REFERRALS.get(user_id, {}).get('balance', 0)
        # Списываем только то, что реально есть на балансе
        final_bonuses = min(applied_bonuses, user_balance)
        
        if final_bonuses > 0:
            REFERRALS[user_id]['balance'] -= final_bonuses
            save_referrals()

    # --- 4. ИТОГОВАЯ ЦЕНА ---
    final_pay = max(0, total_price - discount_amount - final_bonuses)

    # --- 5. СОЗДАНИЕ ЗАКАЗА В БАЗЕ ---
    global NEXT_ORDER_ID
    order_id = NEXT_ORDER_ID
    NEXT_ORDER_ID += 1
    
    username, full_name = get_user_info(call.from_user)
    
    ORDERS[order_id] = {
        "user_id": user_id,
        "username": username,
        "full_name": full_name,
        "items": cart.copy(),
        "total_price": total_price,
        "discount": discount_amount + final_bonuses,
        "final_pay": final_pay,
        "status": "new",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_orders()

    # --- 6. УВЕДОМЛЕНИЕ АДМИНАМ ---
    items_text = ""
    for item in cart:
        items_text += f"• {item['name']} ({item['size']}) — {item['price']} ₽\n"

    admin_text = (
        f"🔔 *НОВЫЙ ЗАКАЗ #{order_id}*\n\n"
        f"👤 Клиент: {full_name}\n"
        f"📱 Username: {username}\n"
        f"🆔 ID: `{user_id}`\n\n"
        f"🛍 *Товары:*\n{items_text}\n"
        f"💰 Сумма: {total_price} ₽\n"
        f"🎫 Скидка: {discount_amount + final_bonuses} ₽\n"
        f"✅ *ИТОГО К ОПЛАТЕ: {final_pay} ₽*"
    )

    # Кнопка для связи
    mk_admin = types.InlineKeyboardMarkup()
    mk_admin.add(types.InlineKeyboardButton("✉️ Написать клиенту", url=f"tg://user?id={user_id}"))

    bot.send_message(ADMIN_GROUP_ID, admin_text, parse_mode="Markdown", reply_markup=mk_admin)

    # --- 7. ОТВЕТ КЛИЕНТУ ---
    bot.answer_callback_query(call.id, "✅ Заказ успешно оформлен!")
    
    client_text = (
        f"✅ *Заказ #{order_id} принят!*\n\n"
        f"Сумма к оплате: *{final_pay} ₽*\n\n"
        f"Менеджер свяжется с вами в ближайшее время для уточнения деталей оплаты и доставки. Спасибо! ❤️"
    )
    
    bot.send_message(call.message.chat.id, client_text, parse_mode="Markdown")

    # --- 8. ОЧИСТКА ДАННЫХ ---
    CARTS[user_id] = []
    user_data[user_id]['applied_promo'] = None
    user_data[user_id]['applied_bonuses'] = 0
    # Возвращаем в главное меню
    send_welcome(call.message.chat.id, user_id)


@bot.callback_query_handler(func=lambda c: c.data == "cart_clear")
def cart_clear_handler(call):
    user_id = call.from_user.id
    
    if user_id in CARTS:
        del CARTS[user_id]
        bot.answer_callback_query(call.id, "🧹 Корзина очищена!")
    else:
        bot.answer_callback_query(call.id, "Корзина и так пуста")
   
    # Показываем пустую корзину
    send_one_msg(
        call.message.chat.id,
        "🛒 Ваша корзина пуста",
        reply_markup=get_reply_main_menu(),
        user_id=user_id
    )

# --- ОБРАБОТЧИКИ CALLBACK-КНОПОК ---

@bot.callback_query_handler(func=lambda c: c.data == "admin_cancel")
def admin_cancel(call):
    user_id = call.from_user.id
    if user_id in user_data:
        prev_waiting = user_data[user_id].get('waiting_for')
        # Очищаем только состояние ожидания, не весь объект
        user_data[user_id]['waiting_for'] = None
        user_data[user_id].pop('temp_cat', None)
        user_data[user_id].pop('temp_sub', None)
        user_data[user_id].pop('editing_product_id', None)
        user_data[user_id].pop('new_product', None)
        user_data[user_id].pop('promo_tmp', None)
        user_data[user_id].pop('current_ticket_id', None)
        
        logger.info(f"Пользователь {user_id} отменил операцию: {prev_waiting}")
        bot.answer_callback_query(call.id, "⏹️ Операция отменена", show_alert=False)
        
        # Возвращаем в главное меню, если отмена была из определенных операций
        if prev_waiting in ['support_message', 'name_new_shoes', 'name_new_clothes', 'input_promo', 
                           'msg_to_support', 'client_reply_message']:
            send_welcome(call.message.chat.id, user_id)
        elif is_admin(user_id) and prev_waiting and prev_waiting.startswith(('add_', 'wait_', 'prod_', 'del_', 'edit_')):
            # Если админ отменил админскую операцию, возвращаем в админ-панель
            send_one_msg(call.message.chat.id, "🔧 Админ-панель", 
                        reply_markup=get_admin_reply_menu(), user_id=user_id)
    else:
        bot.answer_callback_query(call.id, "⏹️ Нет активных операций", show_alert=False)

@bot.callback_query_handler(func=lambda c: c.data.startswith("faq_"))
def faq_handler(call):
    try:
        index = int(call.data.split("_")[1])
        text = FAQ_ANSWERS[index]
        
        mk = types.InlineKeyboardMarkup(row_width=1)
        mk.add(types.InlineKeyboardButton("◀️ Назад к FAQ", callback_data="back_faq"))
        
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=mk
        )
    except Exception as e:
        logger.error(f"Ошибка в faq_handler: {e}")
        bot.answer_callback_query(call.id, "⚠️ Произошла ошибка.")

@bot.callback_query_handler(func=lambda c: c.data == "back_main")
def back_main_handler(call):
    """Обработчик кнопки 'Главное меню' - всегда возвращает в главное меню клиента"""
    user_id = call.from_user.id
    
    # Очищаем состояние пользователя
    if user_id in user_data:
        user_data[user_id]['waiting_for'] = None
        user_data[user_id]['current_cat'] = None
        user_data[user_id]['current_sub'] = None
        user_data[user_id].pop('temp_cat', None)
        user_data[user_id].pop('temp_sub', None)
    
    # Всегда возвращаем в главное меню (не в админ-панель)
    send_welcome(call.message.chat.id, user_id)
    bot.answer_callback_query(call.id)


# --- КАТАЛОГ И НАВИГАЦИЯ (ИСПРАВЛЕНО) ---

@bot.callback_query_handler(func=lambda c: c.data.startswith("cat_"))
def cat_handler(call):
    try:
        category = call.data.split("_")[1]
        mk = size_menu(category)
        if not mk:
            bot.answer_callback_query(call.id, "Товары скоро появятся!")
            return

        text_or_caption = f"{'👟 Выберите размер обуви:' if category == 'shoes' else '👕 Выберите размер одежды:'}"

        # Пытаемся отредактировать caption (если было фото)
        try:
            bot.edit_message_caption(
                caption=text_or_caption,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=mk
            )
        except:
            # Если не фото — редактируем текст
            try:
                bot.edit_message_text(
                    text=text_or_caption,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=mk
                )
            except:
                # Если не удалось — удаляем и отправляем заново
                try:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                except:
                    pass
                bot.send_message(
                    call.message.chat.id,
                    text_or_caption,
                    reply_markup=mk
                )
        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"Ошибка в cat_handler: {e}")
        bot.answer_callback_query(call.id, "⚠️ Не удалось вернуться к размерам.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("select_size_"))
def select_size_handler(call):
    try:
        # select_size_{category}_{size}
        parts = call.data.split("_")
        category = parts[2]
        size = "_".join(parts[3:])
        
        # Удаляем старое сообщение и показываем первый товар
        bot.delete_message(call.message.chat.id, call.message.message_id)
        show_browse(call, category, size, 0)
        
    except Exception as e:
        logger.error(f"Ошибка в select_size_handler: {e}")
        bot.answer_callback_query(call.id, "⚠️ Произошла ошибка при выборе размера.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("sel_p_"))
def back_to_product_sizes_callback(call):
    # Данные: sel_p_ID
    pid = int(call.data.split("_")[2])
    p = find_product_by_id(pid)
    if p:
        # Возвращаем вид "Выберите размер"
        mk = types.InlineKeyboardMarkup(row_width=3)
        for s in p['sizes']:
            mk.add(types.InlineKeyboardButton(s, callback_data=f"view_size_{p['id']}_{s}"))
        mk.add(types.InlineKeyboardButton("◀️ В меню", callback_data="back_main"))
        
        caption = f"🎁 *{p['name']}*\n💰 Цена: {p['price']} ₽\n\nВыберите размер для заказа:"
        bot.edit_message_caption(caption, call.message.chat.id, call.message.message_id, reply_markup=mk, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("browse_"))
def browse_handler(call):
    try:
        # browse_{category}_{size}_{idx}
        parts = call.data.split("_")
        category = parts[1]
        size = parts[2]
        idx = int(parts[3])
        
        # Удаляем старое сообщение и показываем следующий/предыдущий товар
        bot.delete_message(call.message.chat.id, call.message.message_id)
        show_browse(call, category, size, idx)
        
    except Exception as e:
        logger.error(f"Ошибка в browse_handler: {e}")
        bot.answer_callback_query(call.id, "⚠️ Произошла ошибка при навигации.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("order_") and not c.data.startswith(("order_view_", "order_status_", "order_message_")))
def order_from_product_handler(call):
    """Оформление быстрого заказа из каталога: order_{product_id}_{size}"""
    try:
        parts = call.data.split("_")
        if len(parts) != 3:
            return  # Игнорируем, если не 3 части
        
        _, product_id_str, size = parts
        product_id = int(product_id_str)
        product = find_product_by_id(product_id)
        
        if not product:
            bot.answer_callback_query(call.id, "Товар не найден")
            return
        
        if not product.get('stock', {}).get(size, False):
            bot.answer_callback_query(call.id, "❌ Извините, этот размер только что закончился!", show_alert=True)
            return

        # === ВСЁ ОСТАЛЬНОЕ БЕЗ ИЗМЕНЕНИЙ ===
        user = call.from_user
        if not user.username and not (user.first_name or user.last_name):
            bot.answer_callback_query(call.id, "❌ Установите имя или username в настройках Telegram")
            return

        user_id = call.from_user.id
        if user_id not in user_data:
            user_data[user_id] = {}
        
        last_order_time = user_data[user_id].get("last_order_time", 0)
        current_time = int(call.message.date)
        if current_time - last_order_time < 30:
            bot.answer_callback_query(call.id, "⏳ Подождите 30 секунд перед следующим заказом")
            return
        user_data[user_id]["last_order_time"] = current_time

        global NEXT_ORDER_ID
        order_id = NEXT_ORDER_ID
        NEXT_ORDER_ID += 1
        
        username, full_name = get_user_info(call.from_user)

        # Рассчитываем итоговую сумму
        total_price = product["price"]
        final_pay = total_price
        
        ORDERS[order_id] = {
            "user_id": user_id,
            "username": username,
            "full_name": full_name,
            "items": [{
                "product_id": product_id,
                "size": size,
                "name": product["name"],
                "price": product["price"]
            }],
            "total_price": total_price,
            "discount": 0,
            "final_pay": final_pay,
            "status": "new",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_orders()

        admin_text = (
            f"🔔 *НОВЫЙ ЗАКАЗ #{order_id} (Быстрый заказ)*\n\n"
            f"👤 Клиент: {full_name}\n"
            f"📱 Username: {username}\n"
            f"🆔 ID: `{user_id}`\n\n"
            f"🛍 *Товары:*\n"
            f"• {product['name']} ({size}) — {product['price']} ₽\n\n"
            f"💰 Сумма: {total_price} ₽\n"
            f"✅ *ИТОГО К ОПЛАТЕ: {final_pay} ₽*"
        )

        mk_admin = types.InlineKeyboardMarkup()
        try:
            mk_admin.add(types.InlineKeyboardButton("✉️ Написать в ЛС", callback_data=f"order_message_{order_id}"))
        except Exception as e:
            if "BUTTON_USER_PRIVACY_RESTRICTED" in str(e):
                pass  # Просто без кнопки
            else:
                raise  # Если другая ошибка — поднимаем её

        if user_id in ADMIN_IDS:
            admin_text += "\n⚠️ *Внимание: это тестовый заказ от админа!*"

        try:
            if product.get("image"):
                bot.send_photo(ADMIN_GROUP_ID, product["image"], caption=admin_text, parse_mode="Markdown", reply_markup=mk_admin)
            else:
                bot.send_message(ADMIN_GROUP_ID, admin_text, parse_mode="Markdown", reply_markup=mk_admin)
            bot.answer_callback_query(call.id, "✅ Заказ отправлен!")
        except Exception as e:
            logger.error(f"Ошибка отправки заказа в группу: {e}")
            bot.answer_callback_query(call.id, "Не удалось отправить заказ")

        client_text = (
            f"✅ *Заказ #{order_id} принят!*\n\n"
            f"Сумма к оплате: *{final_pay} ₽*\n\n"
            f"Менеджер свяжется с вами в ближайшее время для уточнения деталей оплаты и доставки. Спасибо! ❤️"
        )
        mk_client = types.InlineKeyboardMarkup()
        mk_client.add(types.InlineKeyboardButton("◀️ Главное меню", callback_data="back_main"))

        try:
            bot.edit_message_caption(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                caption=client_text,
                parse_mode="Markdown",
                reply_markup=mk_client
            )
        except:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=client_text,
                parse_mode="Markdown",
                reply_markup=mk_client
            )

    except Exception as e:
        logger.error(f"Ошибка в order_from_product_handler: {e}")
        bot.answer_callback_query(call.id, "⚠️ Произошла ошибка при оформлении заказа.")

# --- АДМИН-ПАНЕЛЬ (ИСПРАВЛЕНО) ---

@bot.message_handler(commands=['admin'])
def admin_panel_command(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        bot.send_message(message.chat.id, "❌ У вас нет доступа к админ-панели")
        return
    bot.send_message(
        message.chat.id,
        "🔧 Админ-панель",
        reply_markup=get_admin_reply_menu()
    )



@bot.message_handler(func=lambda message: message.text == "🎫 Промокоды" and is_admin(message.from_user.id))
def admin_promo_menu(message):
    delete_user_msg_delayed(message.chat.id, message.message_id, delay=1)
    if not PROMOCODES:
        text = "*Промокодов пока нет.*"
    else:
        text = "*Управление промокодами:*\nНажмите на код, чтобы его удалить."
    
    mk = types.InlineKeyboardMarkup(row_width=1)
    
    # Создаем кнопку удаления для каждого существующего промокода
    for code, info in PROMOCODES.items():
        type_icon = "%" if info['type'] == 'percent' else "₽"
        btn_text = f"🗑 Удалить: {code} (-{info['value']}{type_icon})"
        mk.add(types.InlineKeyboardButton(btn_text, callback_data=f"admin_promo_del_{code}"))
    
    mk.add(types.InlineKeyboardButton("➕ Добавить новый", callback_data="admin_promo_add"))
    
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_promo_del_"))
def admin_promo_delete_callback(call):
    if not is_admin(call.from_user.id): return
    
    code_to_del = call.data.replace("admin_promo_del_", "")
    
    if code_to_del in PROMOCODES:
        del PROMOCODES[code_to_del]
        save_promocodes()
        bot.answer_callback_query(call.id, f"✅ Промокод {code_to_del} удален!")
        # Обновляем список промокодов в том же сообщении
        admin_promo_menu(call.message) 
    else:
        bot.answer_callback_query(call.id, "❌ Ошибка: код не найден.")


@bot.callback_query_handler(func=lambda c: c.data == "admin_stats")
def admin_stats(call):
    if not is_admin(call.from_user.id):
        return
    
    # Собираем данные
    total_orders = len(ORDERS)
    status_count = {
        'new': 0,
        'in_processing': 0,
        'sent': 0,
        'completed': 0
    }
    total_revenue = 0
    unique_clients = set()
    product_sales = {}  # product_id → (кол-во, сумма)
    
    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day)
    week_start = today_start - timedelta(days=7)
    
    today_orders = 0
    today_revenue = 0
    week_orders = 0
    week_revenue = 0
    
    for order in ORDERS.values():
        order_date = datetime.strptime(order['created_at'], "%Y-%m-%d %H:%M:%S")
        status = order['status']
        # нормализуем статус, если вдруг в БД лежит что-то другое
        status = {
            'in': 'in_processing',      # старый вариант
            'new': 'new',
            'in_processing': 'in_processing',
            'sent': 'sent',
            'completed': 'completed'
        }.get(status, 'new')
        status_count[status] += 1
        
        items_total = sum(item['price'] for item in order['items'])
        total_revenue += items_total
        
        unique_clients.add(order['user_id'])
        
        # За сегодня и неделю
        if order_date >= today_start:
            today_orders += 1
            today_revenue += items_total
        if order_date >= week_start:
            week_orders += 1
            week_revenue += items_total
        
        # Топ товаров
        for item in order['items']:
            pid = item['product_id']
            name = item['name']
            price = item['price']
            key = (pid, name)
            if key not in product_sales:
                product_sales[key] = {'count': 0, 'revenue': 0}
            product_sales[key]['count'] += 1
            product_sales[key]['revenue'] += price
    
    # Средний чек
    avg_check = round(total_revenue / total_orders, 2) if total_orders > 0 else 0
    
    # Топ-5 товаров
    top_products = sorted(product_sales.items(), key=lambda x: x[1]['count'], reverse=True)[:5]
    top_text = ""
    for i, ((pid, name), stats) in enumerate(top_products, 1):
        top_text += f"{i}. {name} — {stats['count']} шт. ({stats['revenue']} ₽)\n"
    if not top_text:
        top_text = "Пока нет продаж"
    
    # Основной текст
    text = (
        f"📊 *Статистика магазина*\n\n"
        f"👥 Уникальных клиентов: *{len(unique_clients)}*\n"
        f"🛍 Всего заказов: *{total_orders}*\n"
        f"   • Новые: {status_count['new']}\n"
        f"   • В обработке: {status_count['in_processing']}\n"
        f"   • Отправленные: {status_count['sent']}\n"
        f"   • Завершённые: {status_count['completed']}\n\n"
        f"💰 Выручка всего: *{total_revenue} ₽*\n"
        f"📈 Средний чек: *{avg_check} ₽*\n\n"
        f"🔥 *Топ-5 товаров:*\n{top_text}\n\n"
        f"📅 За сегодня: {today_orders} заказов / {today_revenue} ₽\n"
        f"🗓 За неделю: {week_orders} заказов / {week_revenue} ₽"
    )
    
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("🔄 Обновить", callback_data="admin_stats"))
    mk.add(types.InlineKeyboardButton("◀️ Назад в админ-панель", callback_data="admin_panel"))
    
    safe_edit_message(call, text, reply_markup=mk)

def safe_edit_message(call, text, reply_markup=None):
    try:
        bot.edit_message_text(
            text=text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        return
    except Exception as e1:
        # Если было фото — попробуем edit caption
        try:
            bot.edit_message_caption(
                caption=text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
            return
        except Exception as e2:
            pass

    # Если ничего не вышло — удаляем старое и шлём новое
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=get_admin_reply_menu())

@bot.callback_query_handler(func=lambda c: c.data == "admin_panel")
def admin_panel_callback(call):
    if not is_admin(call.from_user.id):
        return
    
    user_id = call.from_user.id
    # Очищаем состояние при возврате в админ-панель
    if user_id in user_data:
        user_data[user_id].pop('waiting_for', None)
        user_data[user_id].pop('editing_product_id', None)
        user_data[user_id].pop('temp_cat', None)
        user_data[user_id].pop('temp_sub', None)
    
    safe_edit_message(
        call,
        "🔧 *Админ-панель*",
        reply_markup=get_admin_reply_menu()
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "admin_orders")
def admin_orders_list(call):
    if not is_admin(call.from_user.id):
        return
    
    total_orders = len(ORDERS)
    new_orders = sum(1 for o in ORDERS.values() if o.get('status') == 'new')
    
    text = (
        f"🚚 *Управление заказами*\n\n"
        f"Всего заказов: *{total_orders}*\n"
        f"Новых: *{new_orders}*\n\n"
    )
    
    if total_orders == 0:
        text += "Нет заказов на данный момент."
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("◀️ Назад в админ-панель", callback_data="admin_panel"))
    else:
        text += "Выберите заказ для просмотра:"
        mk = get_orders_list_menu()
    
    # Универсальная функция — пробует все варианты редактирования
    safe_edit_message(call, text, reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data.startswith("order_view_"))
def admin_order_view(call):
    if not is_admin(call.from_user.id):
        return
    try:
        order_id = int(call.data.split("_")[2])
        order = ORDERS.get(order_id)
        if not order:
            bot.answer_callback_query(call.id, "Заказ не найден")
            return

        # Формируем текст заказа
        status_text = {
            'new': '🆕 Новый',
            'in_processing': '🔄 В обработке',
            'sent': '🚚 Отправлен',
            'completed': '✅ Завершён'
        }.get(order['status'], '❓ Неизвестно')

        items_text = ""
        total_price = 0
        for item in order['items']:
            items_text += f"• {item['name']} ({item['size']}) — {item['price']} ₽\n"
            total_price += item['price']

        user_id = order['user_id']

        # Формируем текст
        text = (
            f"📦 *Заказ #{order_id}*\n\n"
            f"Статус: *{status_text}*\n"
            f"Дата: {order['created_at']}\n\n"
            f"👤 Клиент: {order['full_name']}\n"
            f"📱 Username: {order['username']}\n"
            f"🆔 ID: `{user_id}`\n\n"
            f"🛍 *Товары:*\n{items_text}\n"
            f"*Итого: {total_price} ₽*"
        )

        # Создаём разметку БЕЗ кнопки "Написать клиенту"
        markup = types.InlineKeyboardMarkup(row_width=1)

        # Кнопки статуса
        if order['status'] == 'new':
            markup.add(types.InlineKeyboardButton("🔄 В обработке", callback_data=f"order_status_{order_id}_in_processing"))
        elif order['status'] == 'in_processing':
            markup.row(
                types.InlineKeyboardButton("🚚 Отправлен", callback_data=f"order_status_{order_id}_sent"),
                types.InlineKeyboardButton("⬅️ В новые", callback_data=f"order_status_{order_id}_new")
            )
        elif order['status'] == 'sent':
            markup.row(
                types.InlineKeyboardButton("✅ Завершён", callback_data=f"order_status_{order_id}_completed"),
                types.InlineKeyboardButton("⬅️ В обработке", callback_data=f"order_status_{order_id}_in_processing")
            )
        elif order['status'] == 'completed':
            markup.add(types.InlineKeyboardButton("🔄 Вернуть в обработку", callback_data=f"order_status_{order_id}_in_processing"))

        markup.add(types.InlineKeyboardButton("🗑 Удалить заказ", callback_data=f"order_delete_{order_id}"))
        markup.add(types.InlineKeyboardButton("◀️ Назад к списку", callback_data="admin_orders"))

        # Безопасно редактируем/отправляем
        safe_edit_message(call, text, reply_markup=markup)

        bot.answer_callback_query(call.id, "Заказ открыт")

    except Exception as e:
        logger.error(f"Критическая ошибка в admin_order_view: {e}")
        bot.answer_callback_query(call.id, "Ошибка открытия заказа")



@bot.callback_query_handler(func=lambda c: c.data.startswith("cart_add::"))
def cart_add_handler(call):
    """Добавление товара в корзину: cart_add::{product_id}::{size}"""
    try:
        parts = call.data.split("::")
        if len(parts) != 3:
            bot.answer_callback_query(call.id, "Ошибка добавления")
            return
       
        _, product_id_str, size = parts
        product_id = int(product_id_str)
        product = find_product_by_id(product_id)
       
        if not product:
            bot.answer_callback_query(call.id, "Товар не найден")
            return
        
        if not product.get('stock', {}).get(size, False):
            bot.answer_callback_query(call.id, "❌ Этого товара нет в наличии!", show_alert=True)
            return
       
        user_id = call.from_user.id
        if user_id not in CARTS:
            CARTS[user_id] = []
       
        # Проверяем, есть ли уже такой товар с таким размером
        for item in CARTS[user_id]:
            if item['product_id'] == product_id and item['size'] == size:
                bot.answer_callback_query(call.id, "Этот товар с таким размером уже в корзине!")
                return
       
        # Добавляем
        CARTS[user_id].append({
            "product_id": product_id,
            "name": product["name"],
            "price": product["price"],
            "size": size
        })
       
        bot.answer_callback_query(call.id, f"✅ {product['name']} ({size}) добавлен в корзину!")
       
        # Можно обновить сообщение с товаром, чтобы показать актуальные кнопки
        # Или просто оставить как есть — пользователь увидит при переходе в корзину
       
    except Exception as e:
        logger.error(f"Ошибка при добавлении в корзину: {e}")
        bot.answer_callback_query(call.id, "Ошибка при добавлении")


@bot.callback_query_handler(func=lambda c: c.data.startswith("order_status_"))
def admin_order_change_status(call):
    if not is_admin(call.from_user.id):
        return
    
    try:
        parts = call.data.split("_")
        order_id = int(parts[2])
        new_status = parts[3]
        
        order = ORDERS.get(order_id)
        if not order:
            bot.answer_callback_query(call.id, "Заказ не найден")
            return
        
        old_status = order['status']
        order['status'] = new_status
        save_orders()
        
        status_names = {
            'new': '🆕 Новый',
            'in_processing': '🔄 В обработке',
            'sent': '🚚 Отправлен',
            'completed': '✅ Завершён'
        }
        
        bot.answer_callback_query(call.id, f"Статус изменён: {status_names.get(new_status)}")
        
        # Уведомляем клиента о смене статуса (опционально, но круто!)
        status_client_text = {
            'in_processing': '🔄 Ваш заказ взят в обработку!',
            'sent': '🚚 Ваш заказ отправлен!',
            'completed': '✅ Ваш заказ завершён! Спасибо за покупку ❤️'
        }
        if new_status in status_client_text:
            try:
                bot.send_message(order['user_id'], status_client_text[new_status])
            except:
                pass  # Если пользователь заблокировал бота — игнорируем
        
        # Обновляем детали заказа (кнопки и статус)
        fake_call = call
        fake_call.data = f"order_view_{order_id}"
        admin_order_view(fake_call)
        
    except Exception as e:
        logger.error(f"Ошибка при смене статуса заказа: {e}")
        bot.answer_callback_query(call.id, "Ошибка при изменении статуса")






@bot.callback_query_handler(func=lambda c: c.data == "admin_edit_select")
def admin_edit_select_callback(call):
    """Обработчик возврата к выбору категории для редактирования"""
    if not is_admin(call.from_user.id):
        return
    
    user_id = call.from_user.id
    if user_id in user_data:
        user_data[user_id]['waiting_for'] = 'edit_category'
        user_data[user_id].pop('editing_product_id', None)
    
    # Показываем меню выбора категории
    mk = get_admin_category_menu()
    safe_edit_message(
        call,
        "✏️ Выберите категорию для редактирования:",
        reply_markup=mk
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_edit_category_"))
def admin_edit_category(call):
    if not is_admin(call.from_user.id): 
        return
    category = call.data.split("_")[3]  # shoes или clothes
    safe_edit_message(
        call,
        f"✏️ Редактирование {'обуви' if category == 'shoes' else 'одежды'}\n\nВыберите товар:",
        reply_markup=get_admin_edit_menu(category)
    )
    bot.answer_callback_query(call.id)


@bot.message_handler(func=lambda message: message.text == "➕ Добавить товар")
def admin_add_select_reply(message):
    if not is_admin(message.from_user.id):
        return
    user_data[message.from_user.id] = {'waiting_for': 'add_category'}
    bot.send_message(
        message.chat.id,
        "Выберите категорию:",
        reply_markup=get_admin_category_menu()
    )





@bot.message_handler(func=lambda message:
    user_data.get(message.from_user.id, {}).get('waiting_for') == 'delete_product_by_name' and
    " - " in message.text and "₽" in message.text)
def admin_delete_product_by_name_handler(message):
    if not is_admin(message.from_user.id): return
    name = message.text.split(" - ")[0].strip()
    deleted = False
    for cat in PRODUCTS:
        if cat == "welcome" or not isinstance(PRODUCTS[cat], dict): continue
        for subcat in PRODUCTS[cat]:
            for p in PRODUCTS[cat][subcat][:]:
                if p.get('name') == name:
                    PRODUCTS[cat][subcat].remove(p)
                    deleted = True
    if deleted:
        save_products()
        send_one_msg(message.chat.id, f"✅ Товар '{name}' удален!", reply_markup=get_admin_reply_menu())
    else:
        send_one_msg(message.chat.id, "❌ Товар не найден.", reply_markup=get_admin_reply_menu())
    user_data[message.from_user.id]['waiting_for'] = None



@bot.callback_query_handler(func=lambda c: c.data.startswith("open_sub_"))
def open_subcategory_callback(call):
    user_id = call.from_user.id
    sub_name = call.data.replace("open_sub_", "")
    cat_name = user_data[user_id].get('current_cat')
    if cat_name and sub_name in PRODUCTS.get(cat_name, {}):
        show_size_menu_inline(call.message.chat.id, cat_name, sub_name, user_id)
    bot.answer_callback_query(call.id)





# Обработчик нажатия стрелочек листания
@bot.callback_query_handler(func=lambda c: c.data.startswith("brw_"))
def browser_callback(call):
    user_id = call.from_user.id
    parts = call.data.split("_")
    size = parts[1]
    idx = int(parts[2])
    
    cat = user_data[user_id].get('current_cat')
    sub = user_data[user_id].get('current_sub')
    
    if cat and sub:
        show_browse(call.message.chat.id, cat, sub, size, idx, user_id)
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda message: message.text in ["👟 Обувь", "👕 Одежда"] and
                     user_data.get(message.from_user.id, {}).get('waiting_for') == 'add_category')
def admin_add_category_reply(message):
    if not is_admin(message.from_user.id):
        return
    category = "shoes" if message.text == "👟 Обувь" else "clothes"
    user_data[message.from_user.id] = {'waiting_for': f'name_new_{category}'}
    bot.send_message(message.chat.id, f"➕ Добавление {category}\n\nВведите название товара:")


@bot.message_handler(func=lambda message: message.text == "👕 Одежда" and user_data.get(message.from_user.id, {}).get('waiting_for') == 'edit_category')
def admin_edit_clothes_reply(message):
    if not is_admin(message.from_user.id):
        return
    bot.send_message(
        message.chat.id,
        "Выберите товар для редактирования:",
        reply_markup=get_admin_edit_products_reply_menu("clothes")
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_del_prod_"))
def admin_delete_product_callback(call):
    if not is_admin(call.from_user.id):
        return

    product_id = int(call.data.split("_")[3])
    deleted = False

    for category in ["shoes", "clothes"]:
        for product in PRODUCTS.get(category, []):
            if isinstance(product, dict) and product.get('id') == product_id:
                PRODUCTS[category].remove(product)
                deleted = True
                save_products()
                bot.answer_callback_query(call.id, "✅ Товар удалён!")
                bot.edit_message_text(
                    "✅ Товар удалён!",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=get_admin_reply_menu()
                )
                break
        if deleted:
            break

@bot.message_handler(func=lambda message: message.text == "🗑 Удалить товар")
def admin_delete_select_reply(message):
    if not is_admin(message.from_user.id):
        return
    user_data[message.from_user.id] = {'waiting_for': 'delete_product_by_name'}
    bot.send_message(
        message.chat.id,
        "Выберите товар для удаления:",
        reply_markup=get_admin_delete_products_reply_menu()
    )



def get_admin_delete_products_reply_menu():
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    
    has_products = False
    for cat in PRODUCTS:
        if cat == "welcome": continue
        
        # Если в категории подкатегории (твой случай сейчас)
        if isinstance(PRODUCTS[cat], dict):
            for subcat in PRODUCTS[cat]:
                for product in PRODUCTS[cat][subcat]:
                    mk.add(f"{product['name']} - {product['price']} ₽")
                    has_products = True
        # Если в категории сразу список товаров
        elif isinstance(PRODUCTS[cat], list):
            for product in PRODUCTS[cat]:
                mk.add(f"{product['name']} - {product['price']} ₽")
                has_products = True

    if not has_products:
        bot.send_message(6539363874, "⚠️ В базе данных пока нет товаров.") # Сообщение тебе в консоль/чат
        
    mk.add("◀️ Назад")
    return mk





@bot.message_handler(func=lambda message: message.text == "🖼 Приветствие")
def admin_set_welcome_reply(message):
    if not is_admin(message.from_user.id):
        return
    user_data[message.from_user.id] = {'waiting_for': 'welcome_media'}
    bot.send_message(
        message.chat.id,
        "Отправьте фото/видео/гиф с подписью — это будет приветствие."
    )


@bot.message_handler(func=lambda message: message.text == "📊 Статистика")
def admin_stats_reply(message):
    if not is_admin(message.from_user.id):
        return

    # Собираем данные (копируй из admin_stats)
    total_orders = len(ORDERS)
    new_orders = sum(1 for o in ORDERS.values() if o.get('status') == 'new')
    # ... (остальной код)

    bot.send_message(
        message.chat.id,
        f"📊 *Статистика*\nВсего заказов: {total_orders}\nНовых: {new_orders}",
        parse_mode="Markdown",
        reply_markup=get_admin_reply_menu()
    )


@bot.message_handler(func=lambda message: message.text == "🚚 Заказы")
def admin_orders_reply(message):
    if not is_admin(message.from_user.id):
        return

    total_orders = len(ORDERS)
    new_orders = sum(1 for o in ORDERS.values() if o.get('status') == 'new')

    bot.send_message(
        message.chat.id,
        f"🚚 *Заказы*\nВсего: {total_orders}\nНовых: {new_orders}",
        parse_mode="Markdown",
        reply_markup=get_orders_list_menu()  # пока оставим Inline, если не хочешь переделывать
    )

# --- ЛОГИКА ПОДДЕРЖКИ ---

@bot.callback_query_handler(func=lambda c: c.data.startswith("support_take::"))
def support_take_handler(call):
    if not is_admin(call.from_user.id): return
    try:
        ticket_id = int(call.data.split("::")[1])
        ticket = SUPPORT_TICKETS.get(ticket_id)
        
        if not ticket or ticket['status'] != 'new':
            bot.answer_callback_query(call.id, "⚠️ Тикет уже в работе или закрыт.")
            return
        
        ticket['status'] = 'in_work'
        admin_name = call.from_user.first_name or "Админ"
        
        # 1. Обновляем сообщение в группе
        new_text = call.message.text.replace("Статус: *Новый*", f"Статус: *В работе* (Менеджер: {admin_name})")
        bot.edit_message_text(
            new_text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=get_support_admin_menu(ticket_id, 'in_work')
        )
        
        # 2. Уведомляем клиента
        client_text = f"✅ *Тикет #{ticket_id} взят в работу!*\nМенеджер *{admin_name}* скоро свяжется с вами."
        bot.send_message(ticket['user_id'], client_text, parse_mode="Markdown")
        
        bot.answer_callback_query(call.id, f"Тикет #{ticket_id} взят в работу.")
        
    except Exception as e:
        logger.error(f"Ошибка в support_take_handler: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка при взятии тикета в работу.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("support_reply::"))
def support_reply_admin_handler(call):
    if not is_admin(call.from_user.id): return
    try:
        ticket_id = int(call.data.split("::")[1])
        ticket = SUPPORT_TICKETS.get(ticket_id)
        
        if not ticket or ticket['status'] != 'in_work':
            bot.answer_callback_query(call.id, "⚠️ Тикет не в работе.")
            return
        
        user_id = call.from_user.id
        if user_id not in user_data:
            user_data[user_id] = {}
        user_data[user_id]['waiting_for'] = f'msg_to_support::{ticket_id}'
        
        # Отправляем сообщение админу, чтобы он ввел ответ
        bot.send_message(
            call.message.chat.id,
            f"✍️ *Введите ответ для клиента по тикету #{ticket_id}:*",
            parse_mode="Markdown",
            reply_to_message_id=call.message.message_id,
            reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ Отмена", callback_data="admin_cancel"))
        )
        
        bot.answer_callback_query(call.id, "Ожидаю ваш ответ...")
        
    except Exception as e:
        logger.error(f"Ошибка в support_reply_admin_handler: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка при подготовке ответа.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("support_close::"))
def support_close_handler(call):
    if not is_admin(call.from_user.id): return
    try:
        ticket_id = int(call.data.split("::")[1])
        ticket = SUPPORT_TICKETS.get(ticket_id)
        
        if not ticket or ticket['status'] == 'closed':
            bot.answer_callback_query(call.id, "⚠️ Тикет уже закрыт.")
            return
        
        ticket['status'] = 'closed'
        
        # 1. Обновляем сообщение в группе
        admin_name = call.from_user.first_name or "Админ"
        new_text = call.message.text.replace("Статус: *В работе*", f"Статус: *Закрыт* (Менеджер: {admin_name})")
        bot.edit_message_text(
            new_text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=None # Убираем кнопки
        )
        
        # 2. Уведомляем клиента и ставим кулдаун
        client_id = ticket['user_id']
        client_text = f"✅ *Тикет #{ticket_id} закрыт!*\nПроблема решена. Вы сможете создать новый тикет через 5 минут."
        bot.send_message(client_id, client_text, parse_mode="Markdown")
        
        # Устанавливаем кулдаун
        if client_id not in user_data:
            user_data[client_id] = {}
        user_data[client_id]['support_cooldown_until'] = datetime.now() + timedelta(seconds=SUPPORT_COOLDOWN_SECONDS)
        
        bot.answer_callback_query(call.id, f"Тикет #{ticket_id} закрыт. Клиенту установлен кулдаун.")
        
    except Exception as e:
        logger.error(f"Ошибка в support_close_handler: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка при закрытии тикета.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("client_reply::"))
def client_reply_callback(call):
    try:
        ticket_id = int(call.data.split("::")[1])
        ticket = SUPPORT_TICKETS.get(ticket_id)
        
        if not ticket or ticket['status'] != 'in_work':
            bot.answer_callback_query(call.id, "⚠️ Тикет не активен или закрыт.")
            return
        
        user_id = call.from_user.id
        if user_id not in user_data:
            user_data[user_id] = {}
        
        # Устанавливаем состояние ожидания ответа клиента
        user_data[user_id]['waiting_for'] = 'client_reply_message'
        user_data[user_id]['current_ticket_id'] = ticket_id
        
        # Отправляем сообщение клиенту, чтобы он ввел ответ
        bot.send_message(
            call.message.chat.id,
            f"✍️ *Введите ваше сообщение по тикету #{ticket_id}:*",
            parse_mode="Markdown",
            reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ Отмена", callback_data="admin_cancel"))
        )
        
        bot.answer_callback_query(call.id, "Ожидаю ваше сообщение...")
        
    except Exception as e:
        logger.error(f"Ошибка в client_reply_callback: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка при подготовке ответа.")

# --- ОБРАБОТЧИКИ КОМАНД ---

@bot.message_handler(content_types=['photo', 'video', 'animation'])
def handle_media_admin(message):
    user_id = message.from_user.id
    wf = user_data.get(user_id, {}).get('waiting_for')
    
    if not wf:
        return

    # Получаем ID файла
    if message.content_type == 'photo':
        file_id = message.photo[-1].file_id
    elif message.content_type == 'video':
        file_id = message.video.file_id
    else:
        file_id = message.animation.file_id

    # --- СЦЕНАРИЙ 1: ПРИВЕТСТВИЕ ---
    if wf == "welcome_media":
        PRODUCTS["welcome"] = {
            "type": message.content_type,
            "file_id": file_id,
            "caption": message.caption or ""
        }
        save_products()
        send_one_msg(message.chat.id, "✅ Приветствие обновлено!", reply_markup=get_admin_reply_menu(), user_id=user_id)
        user_data[user_id]['waiting_for'] = None

    # --- СЦЕНАРИЙ 2: РЕДАКТИРОВАНИЕ ФОТО ТОВАРА ---
    elif wf and wf.startswith('photo_edit_'):
        try:
            product_id = int(wf.split("_")[2])
            product = find_product_by_id(product_id)
            if not product:
                bot.send_message(message.chat.id, "❌ Товар не найден.")
                user_data[user_id]['waiting_for'] = None
                return
            
            product['image'] = file_id
            save_products()
            bot.send_message(message.chat.id, "✅ Фото товара обновлено!", reply_markup=get_admin_reply_menu())
            user_data[user_id]['waiting_for'] = None
            user_data[user_id].pop('editing_product_id', None)
        except Exception as e:
            logger.error(f"Ошибка при обновлении фото товара: {e}")
            bot.send_message(message.chat.id, "❌ Ошибка при обновлении фото.")
            user_data[user_id]['waiting_for'] = None
    
    # --- СЦЕНАРИЙ 3: НОВЫЙ ТОВАР (ФИНАЛ) ---
    elif wf == 'photo_new_item_final':
        p = user_data[user_id].get('new_product')
        if not p:
            bot.send_message(message.chat.id, "❌ Ошибка данных. Начните создание товара заново.")
            return
            
        p['image'] = file_id
        cat = p['category']
        sub = p['subcategory']
        
        # Сохраняем в PRODUCTS[Категория][Подкатегория]
        if cat in PRODUCTS and sub in PRODUCTS[cat]:
            PRODUCTS[cat][sub].append(p)
            save_products()
            bot.send_message(message.chat.id, f"✅ Товар '{p['name']}' успешно добавлен в раздел {sub}!", reply_markup=get_admin_reply_menu())
        else:
            bot.send_message(message.chat.id, "❌ Ошибка: категория или подкатегория не найдены.")
            
        user_data[user_id]['waiting_for'] = None
        user_data[user_id].pop('new_product', None)


@bot.message_handler(func=lambda message: message.text == "👥 Рефералы")
def referral_menu(message):
    delete_user_msg_delayed(message.chat.id, message.message_id, delay=1)

    user_id = message.from_user.id
    if user_id not in REFERRALS:
        REFERRALS[user_id] = {"invited_by": None, "balance": 0, "invited_count": 0}
        save_referrals()
    
    data = REFERRALS[user_id]
    bot_info = bot.get_me()
    # Создаем реферальную ссылку
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
    
    text = (
        f"👥 *Реферальная система*\n\n"
        f"Приглашайте друзей и получайте бонусы на покупки!\n\n"
        f"📈 Ваша статистика:\n"
        f"• Приглашено друзей: {data['invited_count']}\n"
        f"• Ваш бонусный баланс: {data['balance']} ₽\n\n"
        f"🔗 Ваша ссылка для приглашения:\n`{ref_link}`\n\n"
        f"_За каждого приглашенного вы получаете 500 ₽ на бонусный баланс!_"
    )
    
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("🔗 Скопировать ссылку", callback_data="copy_ref"))
    mk.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_main"))
    
    send_one_msg(message.chat.id, text, reply_markup=mk, user_id=user_id)

@bot.callback_query_handler(func=lambda c: c.data == "copy_ref")
def copy_ref_callback(call):
    bot.answer_callback_query(call.id, "Просто зажмите ссылку в сообщении, чтобы скопировать её!", show_alert=True)


@bot.message_handler(commands=['debug'])
def debug_state(message):
    user_id = message.from_user.id
    state = user_data.get(user_id, {})
    bot.send_message(
        message.chat.id,
        f"🧪 Debug:\nuser_id: {user_id}\nstate: {json.dumps(state, ensure_ascii=False, indent=2)}"
    )

# --- ОБРАБОТЧИК ТЕКСТА (ПОСЛЕДНИЙ) ---
@bot.message_handler(func=lambda message: True)
def handle_text(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    # Очистка чата от сообщения пользователя
    delete_user_msg_delayed(message.chat.id, message.message_id, delay=1)
    
    if user_id not in user_data: 
        user_data[user_id] = {}
    
    wf = user_data[user_id].get('waiting_for')

    # ==========================================
    # 0. УНИВЕРСАЛЬНАЯ ЛОГИКА КНОПКИ "НАЗАД"
    # ==========================================
    if text == "◀️ Назад":
        user_data[user_id]['waiting_for'] = None
        user_data[user_id].pop('temp_cat', None)
        user_data[user_id].pop('temp_sub', None)
        
        # Если клиент находится в каталоге (выбрана категория), возвращаем его назад по навигации
        if user_data[user_id].get('current_cat'):
            if user_data[user_id].get('current_sub') and user_data[user_id].get('current_sub') != "Модели":
                # Возврат из подкатегории одежды к списку подкатегорий
                user_data[user_id]['current_sub'] = None
                cat = user_data[user_id].get('current_cat')
                subcats = PRODUCTS.get(cat, {})
                mk = types.ReplyKeyboardMarkup(resize_keyboard=True)
                for s in subcats.keys(): mk.add(s)
                mk.add("◀️ Назад")
                send_one_msg(message.chat.id, f"📂 Разделы в {cat}:", reply_markup=mk, user_id=user_id)
            else:
                # Из корня категории или из обуви — в главное меню
                user_data[user_id]['current_cat'] = None
                user_data[user_id]['current_sub'] = None
                send_welcome(message.chat.id, user_id)
            return

        # Если админ И мы не в каталоге (current_cat is None) — возвращаем в админ-панель
        if is_admin(user_id):
            send_one_msg(message.chat.id, "🔧 Админ-панель", 
                         reply_markup=get_admin_reply_menu(), user_id=user_id)
            return

        # Если обычный юзер и мы не в каталоге (по идее сюда попадаем если current_cat был None)
        send_welcome(message.chat.id, user_id)
        return
    
    # ==========================================
    # 0.1. ОБРАБОТКА "ГЛАВНОЕ МЕНЮ" (ВСЕГДА В ГЛАВНОЕ МЕНЮ КЛИЕНТА)
    # ==========================================
    if text == "◀️ Главное меню":
        # Очищаем все состояния
        user_data[user_id]['waiting_for'] = None
        user_data[user_id]['current_cat'] = None
        user_data[user_id]['current_sub'] = None
        user_data[user_id].pop('temp_cat', None)
        user_data[user_id].pop('temp_sub', None)
        
        # ВСЕГДА возвращаем в главное меню клиента (не в админ-панель)
        send_welcome(message.chat.id, user_id)
        return

    # ==========================================
    # 1. ОБРАБОТКА ШАГОВ ВВОДА (WAITING_FOR)
    # ==========================================
    if wf:
        # --- ПРОМОКОДЫ (СОЗДАНИЕ АДМИНОМ) ---
        if wf == 'add_promo_name':
            code = text.upper().strip()
            if not code:
                send_one_msg(message.chat.id, "❌ Название промокода не может быть пустым. Введите название:", user_id=user_id)
                return
            
            if code in PROMOCODES:
                send_one_msg(message.chat.id, f"⚠️ Промокод `{code}` уже существует! Введите другое название:", user_id=user_id)
                return
            
            user_data[user_id]['promo_tmp'] = {'name': code}
            user_data[user_id]['waiting_for'] = 'add_promo_val'
            send_one_msg(message.chat.id, f"💰 Введите сумму скидки для `{code}`:", user_id=user_id)
            return
        if wf == 'add_promo_val':
            if text.isdigit():
                value = int(text)
                if value > 0:
                    user_data[user_id]['promo_tmp']['value'] = value
                    user_data[user_id]['waiting_for'] = 'add_promo_limit'
                    send_one_msg(message.chat.id, "📏 Сколько раз можно использовать код?", user_id=user_id)
                else:
                    send_one_msg(message.chat.id, "❌ Значение должно быть больше 0. Введите корректное значение:", user_id=user_id)
            else:
                send_one_msg(message.chat.id, "❌ Введите корректное число:", user_id=user_id)
            return
        if wf == 'add_promo_limit':
            if text.isdigit():
                limit = int(text)
                if limit > 0:
                    tmp = user_data[user_id]['promo_tmp']
                    PROMOCODES[tmp['name']] = {"type": "fixed", "value": tmp['value'], "left": limit}
                    save_promocodes()
                    user_data[user_id]['waiting_for'] = None
                    user_data[user_id].pop('promo_tmp', None)
                    send_one_msg(message.chat.id, f"✅ Промокод `{tmp['name']}` создан!", reply_markup=get_admin_reply_menu(), user_id=user_id)
                else:
                    send_one_msg(message.chat.id, "❌ Лимит должен быть больше 0. Введите корректное значение:", user_id=user_id)
            else:
                send_one_msg(message.chat.id, "❌ Введите корректное число:", user_id=user_id)
            return

        # --- КЛИЕНТ ВВОДИТ ПРОМОКОД --
        if wf == 'input_promo':
            user_data[user_id]['waiting_for'] = None
            code = text.upper().strip()
            if not code:
                bot.send_message(message.chat.id, "❌ Промокод не может быть пустым. Введите промокод:")
                return
        if code in PROMOCODES:
            promo = PROMOCODES[code]
            if promo['left'] > 0:
                user_data[user_id]['applied_promo'] = code
                bot.send_message(message.chat.id, f"✅ Промокод `{code}` применён!")
            else:
                bot.send_message(message.chat.id, "❌ Промокод исчерпан (лимит использований достигнут).")
        else:
            bot.send_message(message.chat.id, "❌ Неверный промокод. Проверьте правильность ввода.")
            show_cart(message)
            return
        # --- ПОДДЕРЖКА (СОЗДАНИЕ ТИКЕТА) ---
        if wf == 'support_message':
            global NEXT_TICKET_ID
            tid = NEXT_TICKET_ID
            NEXT_TICKET_ID += 1
            username, full_name = get_user_info(message.from_user)
            SUPPORT_TICKETS[tid] = {"id": tid, "user_id": user_id, "status": "new", "history": [f"👤 Клиент: {text}"]}
            save_tickets()
            mk = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🛠 В работу", callback_data=f"support_take::{tid}"))
            admin_msg = bot.send_message(SUPPORT_GROUP_ID, f"🆘 *ЗАЯВКА #{tid}*\n👤 {full_name}\n📝 {text}", parse_mode="Markdown", reply_markup=mk)
            SUPPORT_TICKETS[tid]['admin_msg_id'] = admin_msg.message_id
            save_tickets()
            bot.send_message(message.chat.id, "✅ Запрос отправлен!")
            user_data[user_id]['waiting_for'] = None
            return
        
        # --- ПОДДЕРЖКА (ОТВЕТ АДМИНА) ---
        if wf and wf.startswith('msg_to_support::'):
            try:
                ticket_id = int(wf.split("::")[1])
                ticket = SUPPORT_TICKETS.get(ticket_id)
                if not ticket:
                    bot.send_message(message.chat.id, "❌ Тикет не найден.")
                    user_data[user_id]['waiting_for'] = None
                    return
                
                # Отправляем ответ клиенту
                client_text = f"👨‍💻 *Ответ поддержки по вашему запросу #{ticket_id}:*\n\n{text}"
                try:
                    bot.send_message(ticket['user_id'], client_text, parse_mode="Markdown")
                except Exception as e:
                    bot.send_message(message.chat.id, f"❌ Не удалось отправить клиенту: {e}")
                    user_data[user_id]['waiting_for'] = None
                    return
                
                # Обновляем историю
                ticket['history'].append(f"👨‍💻 Менеджер: {text}")
                ticket['status'] = 'in_work'
                save_tickets()
                
                bot.send_message(message.chat.id, f"✅ Ответ отправлен клиенту по тикету #{ticket_id}")
                user_data[user_id]['waiting_for'] = None
                return
            except Exception as e:
                logger.error(f"Ошибка при отправке ответа поддержки: {e}")
                bot.send_message(message.chat.id, "❌ Ошибка при отправке ответа.")
                user_data[user_id]['waiting_for'] = None
                return
        
        # --- ПОДДЕРЖКА (ОТВЕТ КЛИЕНТА) ---
        if wf == 'client_reply_message':
            try:
                ticket_id = user_data[user_id].get('current_ticket_id')
                if not ticket_id:
                    bot.send_message(message.chat.id, "❌ Ошибка: не найден ID тикета.")
                    user_data[user_id]['waiting_for'] = None
                    return
                
                ticket = SUPPORT_TICKETS.get(ticket_id)
                if not ticket or ticket['status'] != 'in_work':
                    bot.send_message(message.chat.id, "❌ Тикет не активен или закрыт.")
                    user_data[user_id]['waiting_for'] = None
                    return
                
                # Отправляем сообщение в группу поддержки
                username, full_name = get_user_info(message.from_user)
                support_text = f"💬 *Ответ клиента по тикету #{ticket_id}:*\n👤 {full_name}\n📝 {text}"
                try:
                    bot.send_message(SUPPORT_GROUP_ID, support_text, parse_mode="Markdown")
                except Exception as e:
                    bot.send_message(message.chat.id, f"❌ Не удалось отправить в поддержку: {e}")
                    user_data[user_id]['waiting_for'] = None
                    return
                
                # Обновляем историю
                ticket['history'].append(f"👤 Клиент: {text}")
                save_tickets()
                
                bot.send_message(message.chat.id, f"✅ Ваше сообщение отправлено в поддержку!")
                user_data[user_id]['waiting_for'] = None
                user_data[user_id].pop('current_ticket_id', None)
                return
            except Exception as e:
                logger.error(f"Ошибка при отправке ответа клиента: {e}")
                bot.send_message(message.chat.id, "❌ Ошибка при отправке сообщения.")
                user_data[user_id]['waiting_for'] = None
                return

        # --- СОЗДАНИЕ ТОВАРА (УМНАЯ ЛОГИКА) ---
        if wf == 'prod_cat':
            if text in PRODUCTS:
                user_data[user_id]['temp_cat'] = text
                if "обувь" in text.lower() or "shoes" in text.lower():
                    user_data[user_id]['temp_sub'] = "Модели"
                    if "Модели" not in PRODUCTS[text]: PRODUCTS[text]["Модели"] = []
                    user_data[user_id]['waiting_for'] = 'prod_name'
                    send_one_msg(message.chat.id, "👟 Назовите модель обуви:", reply_markup=types.ReplyKeyboardRemove(), user_id=user_id)
                else:
                    user_data[user_id]['waiting_for'] = 'prod_sub'
                    mk = types.ReplyKeyboardMarkup(resize_keyboard=True)
                    for s in PRODUCTS[text].keys(): mk.add(s)
                    mk.add("◀️ Назад")
                    send_one_msg(message.chat.id, "📁 Выберите раздел одежды:", reply_markup=mk, user_id=user_id)
            return

        if wf == 'prod_sub':
            if text in PRODUCTS.get(user_data[user_id]['temp_cat'], {}):
                user_data[user_id]['temp_sub'] = text
                user_data[user_id]['waiting_for'] = 'prod_name'
                send_one_msg(message.chat.id, "📝 Введите название товара:", reply_markup=types.ReplyKeyboardRemove(), user_id=user_id)
            return

        if wf == 'prod_name':
            if text.strip():
                user_data[user_id]['new_product'] = {
                    'id': get_next_product_id(),
                    'name': text.strip(),
                    'category': user_data[user_id]['temp_cat'],
                    'subcategory': user_data[user_id]['temp_sub']
                }
                user_data[user_id]['waiting_for'] = 'prod_price'
                send_one_msg(message.chat.id, "💰 Введите цену товара (только число):", user_id=user_id)
            else:
                send_one_msg(message.chat.id, "❌ Название не может быть пустым. Введите название товара:", user_id=user_id)
            return
        
        if wf == 'prod_price':
            if text.isdigit():
                price = int(text)
                if price > 0:
                    user_data[user_id]['new_product']['price'] = price
                    user_data[user_id]['waiting_for'] = 'prod_sizes'
                    send_one_msg(message.chat.id, "📏 Размеры (через запятую):", user_id=user_id)
                else:
                    send_one_msg(message.chat.id, "❌ Цена должна быть больше 0. Введите корректную цену:", user_id=user_id)
            else:
                send_one_msg(message.chat.id, "❌ Введите корректную цену (только число):", user_id=user_id)
            return

        if wf == 'prod_sizes':
            sizes = [s.strip() for s in text.split(',') if s.strip()]
            if sizes:
                user_data[user_id]['new_product']['sizes'] = sizes
                user_data[user_id]['new_product']['stock'] = {s: True for s in sizes}
                user_data[user_id]['waiting_for'] = 'photo_new_item_final'
                send_one_msg(message.chat.id, "🖼 Отправьте ФОТО товара:", user_id=user_id)
            else:
                send_one_msg(message.chat.id, "❌ Размеры не могут быть пустыми. Введите размеры через запятую:", user_id=user_id)
            return

        # --- СОЗДАНИЕ КАТЕГОРИИ ---
        if wf == 'wait_cat_name':
            if text.strip():
                cat_name = text.strip()
                if cat_name not in PRODUCTS:
                    PRODUCTS[cat_name] = {}
                    save_products()
                    send_one_msg(message.chat.id, f"✅ Категория '{cat_name}' создана!", reply_markup=get_admin_reply_menu(), user_id=user_id)
                else:
                    send_one_msg(message.chat.id, f"⚠️ Категория '{cat_name}' уже существует!", reply_markup=get_admin_reply_menu(), user_id=user_id)
                user_data[user_id]['waiting_for'] = None
            return
        
        # --- СОЗДАНИЕ ПОДКАТЕГОРИИ (ШАГ 1: ВЫБОР КАТЕГОРИИ) ---
        if wf == 'wait_subcat_cat':
            if text in PRODUCTS and text != "welcome":
                user_data[user_id]['temp_cat'] = text
                user_data[user_id]['waiting_for'] = 'wait_subcat_name'
                send_one_msg(message.chat.id, f"📝 Имя новой подкатегории в '{text}':", reply_markup=types.ReplyKeyboardRemove(), user_id=user_id)
            return
        
        # --- СОЗДАНИЕ ПОДКАТЕГОРИИ (ШАГ 2: ВВОД ИМЕНИ) ---
        if wf == 'wait_subcat_name':
            temp_cat = user_data[user_id].get('temp_cat')
            if temp_cat and temp_cat in PRODUCTS:
                subcat_name = text.strip()
                if subcat_name:
                    if subcat_name not in PRODUCTS[temp_cat]:
                        PRODUCTS[temp_cat][subcat_name] = []
                        save_products()
                        send_one_msg(message.chat.id, f"✅ Подкатегория '{subcat_name}' создана в '{temp_cat}'!", reply_markup=get_admin_reply_menu(), user_id=user_id)
                    else:
                        send_one_msg(message.chat.id, f"⚠️ Подкатегория '{subcat_name}' уже существует!", reply_markup=get_admin_reply_menu(), user_id=user_id)
                    user_data[user_id]['waiting_for'] = None
                    user_data[user_id].pop('temp_cat', None)
            return
        
        # --- УДАЛЕНИЕ ---
        if wf == 'delete_product_by_name':
            admin_delete_product_by_name_handler(message)
            return
        if wf == 'del_cat_final':
            if text in PRODUCTS:
                del PRODUCTS[text]
                save_products()
                send_one_msg(message.chat.id, "🗑 Категория удалена.", reply_markup=get_admin_reply_menu(), user_id=user_id)
            user_data[user_id]['waiting_for'] = None
            return

    # ==========================================
    # 2. ГЛАВНЫЕ КНОПКИ АДМИНА
    # ==========================================
    if is_admin(user_id):
        if text == "➕ Создать":
            send_one_msg(message.chat.id, "Что создаем?", reply_markup=get_admin_create_menu(), user_id=user_id); return
        if text == "🗑 Удалить":
            send_one_msg(message.chat.id, "Что удаляем?", reply_markup=get_admin_delete_menu_root(), user_id=user_id); return
        if text == "📁 Категорию":
            user_data[user_id]['waiting_for'] = 'wait_cat_name'
            send_one_msg(message.chat.id, "📝 Имя новой категории:", reply_markup=types.ReplyKeyboardRemove(), user_id=user_id)
            return
        if text == "📂 Подкатегорию":
            user_data[user_id]['waiting_for'] = 'wait_subcat_cat'
            mk = types.ReplyKeyboardMarkup(resize_keyboard=True)
            for c in PRODUCTS.keys():
                if c != "welcome": mk.add(c)
            mk.add("◀️ Назад")
            send_one_msg(message.chat.id, "📁 Выберите категорию для подкатегории:", reply_markup=mk, user_id=user_id)
            return
        if text == "🎁 Товар":
            user_data[user_id]['waiting_for'] = 'prod_cat'
            mk = types.ReplyKeyboardMarkup(resize_keyboard=True)
            for c in PRODUCTS.keys(): 
                if c != "welcome": mk.add(c)
            mk.add("◀️ Назад"); send_one_msg(message.chat.id, "📁 Выберите категорию:", reply_markup=mk, user_id=user_id); return
        if text == "❌ Удалить Товар":
            user_data[user_id]['waiting_for'] = 'delete_product_by_name'
            send_one_msg(message.chat.id, "🎁 Выберите товар:", reply_markup=get_admin_delete_products_reply_menu(), user_id=user_id); return
        if text == "🎫 Промокоды":
            admin_promo_menu(message); return

    # ==========================================
    # 3. КЛИЕНТСКИЙ МАГАЗИН
    # ==========================================
    if text in PRODUCTS.keys() and text != "welcome":
        user_data[user_id]['current_cat'] = text
        if "обувь" in text.lower() or "shoes" in text.lower():
            user_data[user_id]['current_sub'] = "Модели"
            show_size_menu_inline(message.chat.id, text, "Модели", user_id)
        else:
            subcats = PRODUCTS[text]
            mk = types.ReplyKeyboardMarkup(resize_keyboard=True)
            for s in subcats.keys(): mk.add(s)
            mk.add("◀️ Назад")
            send_one_msg(message.chat.id, f"📂 Разделы в {text}:", reply_markup=mk, user_id=user_id)
        return

    curr_cat = user_data[user_id].get('current_cat')
    if curr_cat and text in PRODUCTS.get(curr_cat, {}):
        user_data[user_id]['current_sub'] = text
        show_size_menu_inline(message.chat.id, curr_cat, text, user_id)
        return

    send_welcome(message.chat.id, user_id)


# 1. Функция показа меню размеров (всегда добавляет кнопку Назад)
def show_size_menu_inline(chat_id, cat, sub, user_id):
    products = PRODUCTS.get(cat, {}).get(sub, [])
    sizes = set()
    for p in products:
        for s in p.get('sizes', []): sizes.add(s)
    
    if not sizes:
        send_one_msg(chat_id, f"❌ В разделе {sub} пока нет товаров.", user_id=user_id)
        return

    mk = types.InlineKeyboardMarkup(row_width=4)
    # Кнопки размеров
    btns = [types.InlineKeyboardButton(s, callback_data=f"sh_sz_{s}_0") for s in sorted(list(sizes))]
    mk.add(*btns)
    mk.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_to_nav"))
    
    send_one_msg(chat_id, f"📏 Выберите размер в {sub}:", reply_markup=mk, user_id=user_id)



@bot.callback_query_handler(func=lambda c: c.data == "back_to_nav")
def back_to_nav_callback(call):
    user_id = call.from_user.id
    if user_id not in user_data:
        user_data[user_id] = {}
    
    try:
        # Сначала отвечаем на callback, чтобы убрать индикатор загрузки
        bot.answer_callback_query(call.id)
        
        sub = user_data[user_id].get('current_sub')
        if sub == "Модели":  # Обувь — идём в главное меню
            user_data[user_id]['current_cat'] = None
            user_data[user_id]['current_sub'] = None
            
            # Удаляем inline-сообщение
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception as e:
                logger.error(f"Ошибка удаления сообщения: {e}")
                # Если не удалось удалить, пытаемся отредактировать
                try:
                    bot.edit_message_text(
                        "🏪 *Добро пожаловать в Orphelins Dorés!*\n\nВыберите категорию:",
                        call.message.chat.id,
                        call.message.message_id,
                        parse_mode="Markdown",
                        reply_markup=get_reply_main_menu()
                    )
                    return
                except:
                    pass
            
            # Очищаем last_bot_msg для этого пользователя, чтобы send_welcome создал новое сообщение
            if user_id in last_bot_msg:
                del last_bot_msg[user_id]
            
            # Отправляем главное меню
            send_welcome(call.message.chat.id, user_id)
            
        else:
            # Одежда — возвращаем к списку подкатегорий
            cat = user_data[user_id].get('current_cat')
            if cat and cat in PRODUCTS:
                user_data[user_id]['current_sub'] = None
                mk = types.ReplyKeyboardMarkup(resize_keyboard=True)
                for s in PRODUCTS.get(cat, {}).keys():
                    mk.add(s)
                mk.add("◀️ Назад")
                
                # Удаляем inline-сообщение
                try:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                except Exception as e:
                    logger.error(f"Ошибка удаления сообщения: {e}")
                    # Если не удалось удалить, пытаемся отредактировать
                    try:
                        bot.edit_message_text(
                            f"📂 Разделы в {cat}:",
                            call.message.chat.id,
                            call.message.message_id,
                            reply_markup=mk
                        )
                        return
                    except:
                        pass
                
                # Очищаем last_bot_msg для этого пользователя
                if user_id in last_bot_msg:
                    del last_bot_msg[user_id]
                
                send_one_msg(call.message.chat.id, f"📂 Разделы в {cat}:", reply_markup=mk, user_id=user_id)
            else:
                # Если категория не найдена, возвращаем в главное меню
                user_data[user_id]['current_cat'] = None
                user_data[user_id]['current_sub'] = None
                try:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                except:
                    pass
                
                # Очищаем last_bot_msg для этого пользователя
                if user_id in last_bot_msg:
                    del last_bot_msg[user_id]
                
                send_welcome(call.message.chat.id, user_id)
    except Exception as e:
        logger.error(f"Ошибка в back_to_nav_callback: {e}")
        try:
            bot.answer_callback_query(call.id, "❌ Ошибка при возврате", show_alert=True)
        except:
            pass


# --- ГАЛЕРЕЯ ЛИСТАНИЯ ТОВАРОВ ---
def show_browse(chat_id, category, subcategory, size, idx, user_id):
    # Фильтруем товары по размеру
    filtered = [p for p in PRODUCTS[category][subcategory] if size in p.get('sizes', [])]
    
    if not filtered:
        bot.send_message(chat_id, "❌ Товаров этого размера больше нет.")
        return

    if idx < 0: idx = 0
    if idx >= len(filtered): idx = len(filtered) - 1
    p = filtered[idx]
    
    caption = (f"📦 *{subcategory}* | Размер: {size}\n\n"
               f"*{p['name']}*\n"
               f"💰 Цена: {p['price']} ₽")

    mk = types.InlineKeyboardMarkup(row_width=3)
    # Ряд навигации
    nav = []
    if idx > 0: nav.append(types.InlineKeyboardButton("⬅️", callback_data=f"brw_{size}_{idx-1}"))
    else: nav.append(types.InlineKeyboardButton(" ", callback_data="none"))
    
    nav.append(types.InlineKeyboardButton(f"{idx+1} / {len(filtered)}", callback_data="none"))
    
    if idx < len(filtered) - 1: nav.append(types.InlineKeyboardButton("➡️", callback_data=f"brw_{size}_{idx+1}"))
    else: nav.append(types.InlineKeyboardButton(" ", callback_data="none"))
    
    mk.add(*nav)
    mk.add(types.InlineKeyboardButton("🛒 В корзину", callback_data=f"cart_add::{p['id']}::{size}"))
    # Кнопка возврата к списку размеров
    mk.add(types.InlineKeyboardButton("◀️ Назад к размерам", callback_data=f"open_sub_{subcategory}"))
    
    # Эффект листания: удаляем старое сообщение, шлем новое
    if user_id and last_bot_msg.get(user_id):
        try: bot.delete_message(chat_id, last_bot_msg[user_id])
        except: pass
    
    mid = bot.send_photo(chat_id, p['image'], caption=caption, parse_mode="Markdown", reply_markup=mk).message_id
    last_bot_msg[user_id] = mid


# --- СПИСОК УДАЛЕНИЯ ТОВАРОВ (ДЛЯ ВСЕХ КАТЕГОРИЙ) ---
# 3. Обработчик возврата из карточки товара к списку размеров
@bot.callback_query_handler(func=lambda c: c.data.startswith("open_sub_"))
def open_subcategory_callback(call):
    user_id = call.from_user.id
    sub_name = call.data.replace("open_sub_", "")
    cat_name = user_data[user_id].get('current_cat')
    if cat_name and sub_name in PRODUCTS.get(cat_name, {}):
        show_size_menu_inline(call.message.chat.id, cat_name, sub_name, user_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "back_to_cats_or_subs")
def back_to_navigation(call):
    user_id = call.from_user.id
    if user_id not in user_data:
        user_data[user_id] = {}
    
    cat = user_data[user_id].get('current_cat')
    sub = user_data[user_id].get('current_sub')
    
    # Если это была обувь (подкатегория "Модели"), возвращаем в главное меню
    if sub == "Модели":
        user_data[user_id]['current_cat'] = None
        user_data[user_id]['current_sub'] = None
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        send_welcome(call.message.chat.id, user_id)
    else:
        # Если одежда — возвращаем к списку подкатегорий
        if cat and cat in PRODUCTS:
            user_data[user_id]['current_sub'] = None
            mk = types.ReplyKeyboardMarkup(resize_keyboard=True)
            for s in PRODUCTS[cat].keys(): 
                mk.add(s)
            mk.add("◀️ Назад")
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
            send_one_msg(call.message.chat.id, f"📂 Разделы в {cat}:", reply_markup=mk, user_id=user_id)
        else:
            # Если категория не найдена, возвращаем в главное меню
            send_welcome(call.message.chat.id, user_id)
    
    bot.answer_callback_query(call.id)

def show_product_card(chat_id, product, user_id):
    mk = types.InlineKeyboardMarkup(row_width=3)
    # Кнопки размеров просто открывают инфо о размере
    for s in product['sizes']:
        mk.add(types.InlineKeyboardButton(s, callback_data=f"view_size_{product['id']}_{s}"))
    mk.add(types.InlineKeyboardButton("◀️ В меню", callback_data="back_main"))
    
    caption = f"🎁 *{product['name']}*\n💰 Цена: {product['price']} ₽\n\nВыберите размер для заказа:"
    send_one_photo(chat_id, product['image'], caption, reply_markup=mk, user_id=user_id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("view_size_"))
def view_size_detail(call):
    try:
        parts = call.data.split("_")
        product_id = int(parts[2])
        size = parts[3]
        
        p = find_product_by_id(product_id)
        if not p:
            bot.answer_callback_query(call.id, "❌ Товар не найден")
            return

        # Безопасное получение стока
        stock = p.get('stock', {})
        in_stock = stock.get(size, True) # Если данных нет, считаем что в наличии
        
        status_text = "✅ В наличии" if in_stock else "❌ Нет в наличии"
        
        caption = (
            f"🎁 *{p['name']}*\n"
            f"📏 Размер: {size}\n"
            f"💰 Цена: {p['price']} ₽\n"
            f"📊 Статус: {status_text}"
        )
        
        mk = types.InlineKeyboardMarkup()
        if in_stock:
            mk.add(types.InlineKeyboardButton("➕ Добавить в корзину", callback_data=f"cart_add::{product_id}::{size}"))
        
        mk.add(types.InlineKeyboardButton("◀️ Назад к размерам", callback_data=f"sel_p_{product_id}"))
        
        bot.edit_message_caption(
            caption=caption,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=mk,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка в view_size_detail: {e}")
        bot.answer_callback_query(call.id, "⚠️ Ошибка при выборе размера")




def repair_database():
    updated = False
    for cat in PRODUCTS:
        if cat == "welcome": continue
        if isinstance(PRODUCTS[cat], dict):
            for subcat in PRODUCTS[cat]:
                for p in PRODUCTS[cat][subcat]:
                    if 'stock' not in p:
                        # Создаем stock на основе списка sizes
                        p['stock'] = {s: True for s in p.get('sizes', [])}
                        updated = True
    if updated:
        save_products()
        print("✅ База данных товаров успешно восстановлена!")

@bot.callback_query_handler(func=lambda c: c.data.startswith("sh_sz_"))
def select_size_and_browse(call):
    user_id = call.from_user.id
    parts = call.data.split("_")
    size = parts[2]
    idx = int(parts[3])
    
    cat = user_data[user_id].get('current_cat')
    sub = user_data[user_id].get('current_sub')
    
    if cat and sub:
        show_browse(call.message.chat.id, cat, sub, size, idx, user_id)
    bot.answer_callback_query(call.id)


# --- ЗАПУСК ---

def check_bot_in_group():
    try:
        # Check if the bot can see the chat (meaning it's a member and has access)
        chat_info = bot.get_chat(ADMIN_GROUP_ID)
        # Check if the bot is a member/admin in the chat
        member = bot.get_chat_member(ADMIN_GROUP_ID, bot.get_me().id)
        return member.status in ['administrator', 'member', 'creator']
    except Exception as e:
        logger.error(f"Ошибка доступа к основной админ-группе: {e}")
        return False

def convert_old_products():
    updated = False
    # Iterate dynamically over all categories except special ones like "welcome"
    for category in PRODUCTS:
        if category == "welcome" or not isinstance(PRODUCTS[category], dict):
            continue
        for subcat in PRODUCTS[category]:
             for product in PRODUCTS[category][subcat]:
                if 'sizes' in product and 'stock' not in product:
                    product['stock'] = {size: True for size in product['sizes']}
                    updated = True
    if updated:
        save_products()
        print("✅ Товары обновлены — добавлено поле 'stock'")

def check_bot_in_support_group():
    try:
        chat_info = bot.get_chat(SUPPORT_GROUP_ID)
        member = bot.get_chat_member(SUPPORT_GROUP_ID, bot.get_me().id)
        return member.status in ['administrator', 'member', 'creator']
    except Exception as e:
        logger.error(f"Ошибка доступа к группе поддержки: {e}")
        return False


# Создаём Flask-сервер
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Бот работает!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8000))
    # Disable reloader and debugger to avoid side effects in threads
    app.run(host="0.0.0.0", port=port, use_reloader=False, debug=False)

if __name__ == "__main__":
    try:
        load_products()
        repair_database()
        load_referrals()
        load_promocodes()
        convert_old_products()  # Запустится один раз
        load_orders()

        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        print(f"✅ Flask сервер запущен на порту {os.environ.get('PORT', 8000)}")

        print("✅ Товары и заказы загружены. Бот запускается...")

        # Optional: Check groups permissions before starting
        # Note: This might fail if the bot is not yet running/connected in some environments,
        # but generally safe to call if the token is valid.
        if check_bot_in_group():
            print("✅ Доступ к основной админ-группе подтвержден")
        else:
            print(f"⚠️ Проблемы с основной группой {ADMIN_GROUP_ID}. Проверьте ID и права бота.")

        if check_bot_in_support_group():
            print("✅ Доступ к группе поддержки подтвержден")
        else:
            print(f"⚠️ Проблемы с группой поддержки {SUPPORT_GROUP_ID}. Проверьте ID и права бота.")

        bot.infinity_polling(timeout=10, long_polling_timeout=5)

    except Exception as e:
        logger.error("❌ КРИТИЧЕСКАЯ ОШИБКА:", exc_info=True)
        print("❌ КРИТИЧЕСКАЯ ОШИБКА:")
        print(e)
