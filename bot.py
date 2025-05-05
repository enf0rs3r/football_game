import asyncio
import random
import time
import os
from aiogram import Bot, Dispatcher, types
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# --- SQLAlchemy и PostgreSQL ---
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, BigInteger, String, Integer, Boolean, select, update, delete

# Строка подключения к PostgreSQL

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()

class Player(Base):
    __tablename__ = "players"
    user_id = Column(BigInteger, primary_key=True)
    name = Column(String)
    position = Column(String)
    club = Column(String)
    matches = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    draws = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    goals = Column(Integer, default=0)
    assists = Column(Integer, default=0)
    saves = Column(Integer, default=0)
    tackles = Column(Integer, default=0)
    is_in_squad = Column(Boolean, default=True)
    current_round = Column(Integer, default=1)
    last_match_date = Column(String)

# --- Асинхронные функции работы с БД ---
async def get_player(user_id):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Player).where(Player.user_id == user_id))
        return result.scalar_one_or_none()

async def create_player(user_id, name, position, club, start_date):
    async with AsyncSessionLocal() as session:
        player = Player(
            user_id=user_id, name=name, position=position, club=club,
            last_match_date=start_date
        )
        session.add(player)
        await session.commit()

async def update_player_stats(user_id, **kwargs):
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Player).where(Player.user_id == user_id).values(**kwargs)
        )
        await session.commit()

async def update_player_club(user_id, club):
    await update_player_stats(user_id, club=club)

async def update_player_squad_status(user_id, is_in_squad):
    await update_player_stats(user_id, is_in_squad=is_in_squad)

# --- Инициализция базы ---
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# --- Остальной код бота (пример для /start) ---
# Замените на свой токен бота
CHANNEL_ID = "@football_simulator"

class GameStates(StatesGroup):
    waiting_name = State()
    waiting_position = State()
    waiting_club_choice = State()
    playing = State()

# Список клубов ФНЛ Серебро
FNL_SILVER_CLUBS = {
    "Текстильщик": {"position": 1, "strength": 80},
    "Сибирь": {"position": 2, "strength": 75},
    "Авангард-Курск": {"position": 3, "strength": 70},
    "Динамо-Киров": {"position": 4, "strength": 65},
    "Динамо-Владивосток": {"position": 5, "strength": 60},
    "Динамо-2 Москва": {"position": 6, "strength": 55},
    "Иртыш Омск": {"position": 7, "strength": 50},
    "Калуга": {"position": 8, "strength": 45},
    "Форте": {"position": 9, "strength": 40},
    "Муром": {"position": 10, "strength": 35}
}

# 1. Добавляем список клубов ФНЛ Золото
FNL_GOLD_CLUBS = {
    "Спартак Кс": {"position": 1, "strength": 90},
    "Волга Ул": {"position": 2, "strength": 88},
    "Ленинградец": {"position": 3, "strength": 86},
    "Волгарь": {"position": 4, "strength": 84},
    "Челябинск": {"position": 5, "strength": 82},
    "Родина-2": {"position": 6, "strength": 80},
    "Машук-КМВ": {"position": 7, "strength": 78},
    "Велес": {"position": 8, "strength": 76},
    "Кубань": {"position": 9, "strength": 74},
    "Торпедо Миасс": {"position": 10, "strength": 72}
}

# Добавляем константы для календаря
SEASON_START_MONTH = 9  # Сентябрь
SEASON_END_MONTH = 5    # Май
WINTER_BREAK_START = 12  # Декабрь
WINTER_BREAK_END = 2    # Февраль
DAYS_BETWEEN_MATCHES = 7  # Количество дней между матчами
SEASON_START_DATE = "01.09.2024"  # Начало сезона

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Функция для получения случайных предложений от клубов
def get_random_club_offers():
    clubs = list(FNL_SILVER_CLUBS.keys())
    return random.sample(clubs, 3)

# Функция для создания клавиатуры с предложениями клубов
def get_club_offers_keyboard(offers):
    keyboard = []
    for club in offers:
        keyboard.append([InlineKeyboardButton(
            text=f"🏆 {club}",
            callback_data=f"choose_club_{club}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# Создаем клавиатуру для выбора позиции
def get_position_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🥅 Вратарь", callback_data="position_gk")],
        [InlineKeyboardButton(text="🛡️ Защитник", callback_data="position_def")],
        [InlineKeyboardButton(text="⚽ Нападающий", callback_data="position_fw")]
    ])

# Создаем клавиатуру для главного меню
def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Играть матч", callback_data="play_match")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="show_stats")]
    ])

# Клавиатура для выбора действий во время матча
def get_match_actions_keyboard(position, is_second_phase=False):
    message_id = int(time.time())  # Используем для идентификации актуального сообщения
    if position == "Вратарь":
        if not is_second_phase:
            return InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏃 Выйти на игрока", callback_data=f"action_rush_{message_id}")],
                [InlineKeyboardButton(text="↙️ Прыгнуть влево", callback_data=f"action_left_{message_id}")],
                [InlineKeyboardButton(text="↘️ Прыгнуть вправо", callback_data=f"action_right_{message_id}")]
            ])
        else:
            return InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⚽ Выбить мяч", callback_data=f"action_kick_{message_id}")],
                [InlineKeyboardButton(text="🎯 Выбросить мяч", callback_data=f"action_throw_{message_id}")]
            ])
    elif position == "Защитник":
        if not is_second_phase:
            return get_defender_defense_keyboard()
        else:
            return get_defender_after_defense_keyboard()
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚽ Удар по воротам", callback_data=f"action_shot_{message_id}")],
            [InlineKeyboardButton(text="🎯 Отдать пас", callback_data=f"action_pass_{message_id}")],
            [InlineKeyboardButton(text="🏃 Дриблинг", callback_data=f"action_dribble_{message_id}")]
        ])

def get_continue_keyboard():
    timestamp = int(time.time())  # Добавляем временную метку
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Продолжить", callback_data=f"continue_match_{timestamp}")]
    ])

# Функция проверки подписки
async def check_subscription(user_id: int) -> bool:
    try:
        user_channel_status = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        status = user_channel_status.status
        return status in ['member', 'administrator', 'creator']
    except Exception as e:
        print(f"Ошибка при проверке подписки: {e}")
        return False

# Функция создания клавиатуры с кнопкой подписки
def get_subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на канал", url=f"https://t.me/{CHANNEL_ID[1:]}")],
        [InlineKeyboardButton(text="Проверить подписку", callback_data="check_subscription")]
    ])

# Функция для отправки фото с описанием
async def send_photo_with_text(message, folder, filename, text):
    photo_path = os.path.join('images', folder, filename)
    if os.path.exists(photo_path):
        with open(photo_path, 'rb') as file:
            photo = BufferedInputFile(file.read(), filename=filename)
            await message.answer_photo(photo, caption=text)
    else:
        await message.answer(text)

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    # Проверяем, не идет ли сейчас матч
    data = await state.get_data()
    if data.get('match_state'):
        await message.answer(
            "❌ Сейчас идет матч! Дождитесь его завершения."
        )
        return

    if not await check_subscription(message.from_user.id):
        await message.answer(
            "Для использования бота необходимо подписаться на наш канал!",
            reply_markup=get_subscription_keyboard()
        )
        return
    
    player = await get_player(message.from_user.id)
    if player:
        await state.set_state(GameStates.playing)
        welcome_text = (
            f"👋 Привет, {player.name}!\n\n"
            f"Вы играете за {player.club}\n"
            f"Позиция: {player.position}\n"
            f"{'✅ В стартовом составе' if player.is_in_squad else '❌ Не в заявке'}\n\n"
            "Добро пожаловать в футбольный симулятор!\n"
            "🏆 Побеждай в матчах\n"
            "⭐ Стань легендой футбола!"
        )
        with open("mbappe.png", "rb") as file:
            photo = BufferedInputFile(file.read(), filename="mbappe.png")
            await message.answer_photo(
                photo,
                caption=welcome_text,
                reply_markup=get_main_keyboard()
            )
    else:
        await message.answer("Добро пожаловать в виртуальную футбольную карьеру! Введите ваше имя:")
        await state.set_state(GameStates.waiting_name)

@dp.callback_query(lambda c: c.data == "check_subscription")
async def check_subscription_callback(callback: types.CallbackQuery):
    if await check_subscription(callback.from_user.id):
        player = await get_player(callback.from_user.id)
        if player:
            welcome_text = (
                f"👋 Привет, {player.name}!\n\n"
                "Добро пожаловать в футбольный симулятор!\n"
                "🏆 Побеждай в матчах\n"
                "⭐ Стань легендой футбола!"
            )
            with open("mbappe.png", "rb") as file:
                photo = BufferedInputFile(file.read(), filename="mbappe.png")
                await callback.message.answer_photo(
                    photo,
                    caption=welcome_text,
                    reply_markup=get_main_keyboard()
                )
        else:
            await callback.message.edit_text("Спасибо за подписку! Теперь вы можете использовать бота.")
            await callback.message.answer("Добро пожаловать в виртуальную футбольную карьеру! Введите ваше имя:")
        await callback.answer()
    else:
        await callback.answer("Вы все еще не подписаны на канал!", show_alert=True)

@dp.message(GameStates.waiting_name)
async def process_name(message: types.Message, state: FSMContext):
    if not await check_subscription(message.from_user.id):
        await message.answer(
            "Для продолжения необходимо подписаться на наш канал!",
            reply_markup=get_subscription_keyboard()
        )
        return
    await state.update_data(name=message.text)
    await state.set_state(GameStates.waiting_position)
    await message.answer(
        "Выберите вашу позицию на поле:",
        reply_markup=get_position_keyboard()
    )

@dp.callback_query(lambda c: c.data.startswith('position_'), GameStates.waiting_position)
async def process_position(callback: types.CallbackQuery, state: FSMContext):
    position_map = {
        "position_gk": "Вратарь",
        "position_def": "Защитник",
        "position_fw": "Нападающий"
    }
    
    position = position_map[callback.data]
    user_data = await state.get_data()
    name = user_data['name']
    
    # Сохраняем позицию в состоянии
    await state.update_data(position=position)
    
    # Получаем случайные предложения от клубов
    offers = get_random_club_offers()
    await state.update_data(offers=offers)
    
    await state.set_state(GameStates.waiting_club_choice)
    await callback.message.answer(
        f"Отлично, {name}! Вы выбрали позицию: {position}\n\n"
        "Вам назначен ваш первый агент. Обращение от него:\nПоступили предложения от следующих клубов ФНЛ Серебро:\n\n"
        f"1. {offers[0]}\n"
        f"2. {offers[1]}\n"
        f"3. {offers[2]}\n\n"
        "Выберите клуб, в котором хотите начать карьеру:",
        reply_markup=get_club_offers_keyboard(offers)
    )
    await callback.answer()

def get_initial_player_date():
    """Определяет начальную дату для нового игрока"""
    current_date = datetime.now()
    current_month = current_date.month
    
    # Если сейчас сезон активен, используем текущую дату
    if is_season_active(current_date):
        return current_date.strftime("%Y-%m-%d")
    
    # Если сейчас не сезон, устанавливаем дату на начало следующего сезона
    if current_month < SEASON_START_MONTH:
        # Если до начала сезона, устанавливаем на начало текущего года
        return datetime(current_date.year, SEASON_START_MONTH, 1).strftime("%Y-%m-%d")
    else:
        # Если после окончания сезона, устанавливаем на начало следующего года
        return datetime(current_date.year + 1, SEASON_START_MONTH, 1).strftime("%Y-%m-%d")

@dp.callback_query(lambda c: c.data.startswith('choose_club_'), GameStates.waiting_club_choice)
async def process_club_choice(callback: types.CallbackQuery, state: FSMContext):
    club = callback.data.split('_')[2]
    user_data = await state.get_data()
    name = user_data['name']
    position = user_data['position']  # Получаем позицию из состояния
    
    # Получаем начальную дату для игрока
    start_date = get_initial_player_date()
    await create_player(callback.from_user.id, name, position, club, start_date)
    await state.set_state(GameStates.playing)
    
    welcome_text = (
        f"👋 Привет, {name}!\n\n"
        f"Вы выбрали клуб: {club}\n"
        f"Позиция: {position}\n\n"
        "Добро пожаловать в футбольный симулятор!\n"
        "🏆 Побеждай в матчах\n"
        "⭐ Стань легендой футбола!"
    )
    with open("mbappe.png", "rb") as file:
        photo = BufferedInputFile(file.read(), filename="mbappe.png")
        await callback.message.answer_photo(
            photo,
            caption=welcome_text,
            reply_markup=get_main_keyboard()
        )
    await callback.answer()

async def get_virtual_date(player):
    """Получает виртуальную дату для игрока"""
    last_match_date = player.last_match_date  # last_match_date
    if not last_match_date:
        return datetime.strptime(SEASON_START_DATE, "%d.%m.%Y")  # Начало сезона
    return datetime.strptime(last_match_date, "%d.%m.%Y")

def is_season_active(virtual_date):
    """Проверяет, идет ли сейчас сезон в виртуальном времени"""
    current_month = virtual_date.month
    return (SEASON_START_MONTH <= current_month <= 12) or (1 <= current_month <= SEASON_END_MONTH)

def is_winter_break(virtual_date):
    """Проверяет, идет ли сейчас зимний перерыв в виртуальном времени"""
    current_month = virtual_date.month
    # Зимний перерыв с декабря по январь
    return current_month == 12 or current_month == 1

async def can_play_match(user_id):
    """Проверяет, может ли игрок сыграть матч в виртуальном времени"""
    player = await get_player(user_id)
    if not player:
        return False, "Сначала создайте своего игрока с помощью команды /start"
    
    virtual_date = await get_virtual_date(player)
    
    if not is_season_active(virtual_date):
        return False, "❌ Сезон еще не начался или уже закончился. Следующий сезон начнется в сентябре."
    
    # Если сейчас зимний перерыв, продвигаем дату до февраля
    if is_winter_break(virtual_date):
        # Продвигаем дату до февраля
        new_date = virtual_date.replace(month=2, day=1)
        # Обновляем дату в базе
        await update_player_stats(
            user_id=user_id,
            last_match_date=new_date.strftime("%d.%m.%Y")
        )
        return False, "❌ Сейчас зимний перерыв. Матчи возобновятся в феврале."
    
    # Проверяем, есть ли матч в текущем туре
    current_round = player.current_round if player.matches > 0 else 1
    opponent = get_opponent_by_round(player.club, current_round)
    
    # Если матча нет, продвигаем дату и тур
    while not opponent and current_round <= len(MATCH_CALENDAR):
        current_round += 1
        opponent = get_opponent_by_round(player.club, current_round)
        # Продвигаем дату на неделю
        new_date = virtual_date + timedelta(days=DAYS_BETWEEN_MATCHES)
        if new_date.month == SEASON_START_MONTH and virtual_date.month != SEASON_START_MONTH:
            new_date = new_date.replace(year=new_date.year + 1)
        virtual_date = new_date
        # Обновляем дату в базе
        await update_player_stats(
            user_id=user_id,
            current_round=current_round,
            last_match_date=virtual_date.strftime("%d.%m.%Y")
        )
    
    # Если дошли до конца календаря, начинаем новый
    if current_round > len(MATCH_CALENDAR):
        current_round = 1
        opponent = get_opponent_by_round(player.club, current_round)
    
    return True, ""

async def advance_virtual_date(player):
    """Продвигает виртуальную дату игрока вперед на 7 дней"""
    current_date = await get_virtual_date(player)
    new_date = current_date + timedelta(days=DAYS_BETWEEN_MATCHES)
    
    # Если новый месяц - сентябрь, увеличиваем год
    if new_date.month == SEASON_START_MONTH and current_date.month != SEASON_START_MONTH:
        new_date = new_date.replace(year=new_date.year + 1)
    
    return new_date.strftime("%d.%m.%Y")

# Создаем календарь матчей
def create_calendar():
    clubs = list(FNL_SILVER_CLUBS.keys())
    calendar = []
    # Первый круг
    for i in range(len(clubs)):
        for j in range(i + 1, len(clubs)):
            calendar.append((clubs[i], clubs[j]))
    # Второй круг (домашние матчи меняются местами)
    for i in range(len(clubs)):
        for j in range(i + 1, len(clubs)):
            calendar.append((clubs[j], clubs[i]))
    return calendar

# Глобальный календарь матчей
MATCH_CALENDAR = create_calendar()

# Функция для получения соперника по текущему туру
def get_opponent_by_round(player_club, current_round):
    if current_round > len(MATCH_CALENDAR):
        # Если турнир закончен, начинаем новый
        current_round = 1
    match = MATCH_CALENDAR[current_round - 1]
    if match[0] == player_club:
        return match[1]
    elif match[1] == player_club:
        return match[0]
    return None

@dp.callback_query(lambda c: c.data == "play_match")
async def play_match_callback(callback: types.CallbackQuery, state: FSMContext):
    # Проверяем, не идет ли сейчас матч
    data = await state.get_data()
    if data.get('match_state', {}).get('is_processing'):
        await callback.answer("❌ Сейчас идет матч! Дождитесь его завершения.", show_alert=True)
        return

    if not await check_subscription(callback.from_user.id):
        await callback.message.answer(
            "Для игры необходимо подписаться на наш канал!",
            reply_markup=get_subscription_keyboard()
        )
        return
    
    can_play, message = await can_play_match(callback.from_user.id)
    if not can_play:
        await callback.message.answer(message)
        return
    
    player = await get_player(callback.from_user.id)
    if not player:
        await callback.message.answer(
            "Сначала создайте своего игрока с помощью команды /start",
            reply_markup=get_main_keyboard()
        )
        return
    
    if not player.is_in_squad:
        await callback.message.answer(
            "❌ Вы не в заявке на матч\n"
            "Тренер решил не включать вас в состав на этот матч.\n"
            "Попробуйте сыграть в следующем матче.",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Получаем соперника по календарю
    current_round = player.current_round if player.matches > 0 else 1
    opponent = get_opponent_by_round(player.club, current_round)
    
    if not opponent:
        await callback.message.answer(
            "❌ В этом туре у вас нет матча.\n"
            "Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Вернуться в меню", callback_data="return_to_menu")]
            ])
        )
        new_date = await advance_virtual_date(player)
        await update_player_stats(
            user_id=callback.from_user.id,
            last_match_date=new_date,
            current_round=player.current_round + 1  # Переходим к следующему туру
        )
        return
    
    # Получаем виртуальную дату
    match_date = (await get_virtual_date(player)).strftime("%d.%m.%Y")
    
    # Очищаем предыдущее состояние матча
    await state.set_data({})
    
    match_state = {
        'your_goals': 0,
        'opponent_goals': 0,
        'minute': 0,
        'possession': 50,
        'last_moment_type': None,
        'your_attacks_count': 0,
        'opponent_attacks_count': 0,
        'current_team': player.club,
        'opponent_team': opponent,
        'is_processing': False,
        'round': current_round,
        'date': match_date,
        'position': player.position,
        'is_opponent_attack': player.position in ["Вратарь", "Защитник"],
        'stats': {
            'goals': 0,
            'assists': 0,
            'saves': 0,
            'tackles': 0,
            'clearances': 0,
            'throws': 0
        }
    }
    
    await state.update_data(match_state=match_state)
    
    # Разное начальное сообщение для разных позиций
    if player.position in ["Вратарь", "Защитник"]:
        message = await callback.message.answer(
            f"🏆 Тур {current_round} ФНЛ Серебро\n"
            f"📅 {match_date}\n\n"
            f"Матч начинается! {player.club} против {opponent}\n"
            f"⏱️ 0' минута. 0-0\n\n"
            f"⚠️ {opponent} начинает атаку!",
            reply_markup=get_match_actions_keyboard(player.position)
        )
    else:
        message = await callback.message.answer(
            f"🏆 Тур {current_round} ФНЛ Серебро\n"
            f"📅 {match_date}\n\n"
            f"Матч начинается! {player.club} против {opponent}\n"
            f"⏱️ 0' минута. 0-0\n"
            f"- Матч начинается",
            reply_markup=get_match_actions_keyboard(player.position)
        )
    
    # Сохраняем ID первого сообщения
    match_state['last_message_id'] = message.message_id
    await state.update_data(match_state=match_state)
    
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith('action_'))
async def handle_action(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    match_state = data.get('match_state')
    
    if not match_state:
        await callback.message.answer(
            "Матч не начат. Нажмите 'Играть матч' для начала.",
            reply_markup=get_main_keyboard()
        )
        await callback.answer()
        return
    
    # Проверяем, что кнопка из последнего сообщения
    if callback.message.message_id != match_state.get('last_message_id'):
        await callback.answer(
            "Используйте кнопки из последнего сообщения ⬇️",
            show_alert=True
        )
        return
    
    # Проверяем, не обрабатывается ли уже момент
    if match_state.get('is_processing', False):
        await callback.answer("Дождитесь завершения текущего момента", show_alert=True)
        return
    
    # Устанавливаем флаг обработки
    match_state['is_processing'] = True
    await state.update_data(match_state=match_state)
    
    try:
        action = callback.data.split('_')[1]
        position = match_state['position']
        
        if position == "Вратарь":
            await handle_goalkeeper_save(callback, match_state, state)
        elif position == "Защитник":
            if action == "tackle":
                await handle_defender_tackle(callback, match_state, state)
            elif action == "block":
                await handle_defender_block(callback, match_state, state)
            elif action == "pass_left":
                await handle_defender_pass_left(callback, match_state, state)
            elif action == "pass_right":
                await handle_defender_pass_right(callback, match_state, state)
            elif action == "clear":
                await handle_defender_clearance(callback, match_state, state)
        else:  # Нападающий
            if action == "shot":
                await handle_forward_shot(callback, match_state, state)
            elif action == "pass":
                await handle_forward_pass(callback, match_state, state)
            elif action == "dribble":
                await handle_forward_dribble(callback, match_state, state)
    except Exception as e:
        print(f"Error in handle_action: {e}")
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)
        await callback.answer("Произошла ошибка. Попробуйте еще раз.", show_alert=True)
    finally:
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)
    
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith('defense_'))
async def handle_defense_action(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    match_state = data.get('match_state')
    
    if not match_state:
        await callback.message.answer(
            "Матч не начат. Нажмите 'Играть матч' для начала.",
            reply_markup=get_main_keyboard()
        )
        await callback.answer()
        return
    
    # Проверяем, что кнопка из последнего сообщения
    if callback.message.message_id != match_state.get('last_message_id'):
        await callback.answer(
            "Используйте кнопки из последнего сообщения ⬇️",
            show_alert=True
        )
        return
    
    # Проверяем, не обрабатывается ли уже момент
    if match_state.get('is_processing', False):
        await callback.answer("Дождитесь завершения текущего момента", show_alert=True)
        return
    
    # Устанавливаем флаг обработки
    match_state['is_processing'] = True
    await state.update_data(match_state=match_state)
    
    try:
        action = callback.data.split('_')[1]
        
        if action == "tackle":
            await handle_defender_tackle(callback, match_state, state)
        elif action == "block":
            await handle_defender_block(callback, match_state, state)
        elif action == "pass_left":
            await handle_defender_pass_left(callback, match_state, state)
        elif action == "pass_right":
            await handle_defender_pass_right(callback, match_state, state)
        elif action == "clear":
            await handle_defender_clearance(callback, match_state, state)
    except Exception as e:
        print(f"Error in handle_defense_action: {e}")
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)
        await callback.answer("Произошла ошибка. Попробуйте еще раз.", show_alert=True)
    finally:
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)
    
    await callback.answer()

# Функция для обработки игрового момента
async def handle_goalkeeper_save(callback: types.CallbackQuery, match_state, state: FSMContext):
    action = callback.data.split('_')[1]
    try:
        # Первая фаза - реакция на удар
        if action in ['rush', 'left', 'right']:
            await send_photo_with_text(
                callback.message,
                'defense',
                'save.jpg',
                f"🖐️ {match_state['current_team']} в опасности!\n- Вратарь готовится к спасению"
            )
            await asyncio.sleep(2)
            
            # Случайно определяем направление удара
            shot_direction = random.choice(['rush', 'left', 'right'])
            
            if action == shot_direction:  # Угадал направление
                match_state['stats']['saves'] += 1
                await send_photo_with_text(
                    callback.message,
                    'defense',
                    'save_success.jpg',
                    "✅ Отличный сейв!\n- Вратарь угадал направление удара"
                )
                # Показываем второй набор действий
                message = await callback.message.answer(
                    "Мяч у вратаря. Выберите следующее действие:",
                    reply_markup=get_match_actions_keyboard(match_state['position'], is_second_phase=True)
                )
                # Сохраняем ID сообщения с кнопками второго этапа
                match_state['last_message_id'] = message.message_id
                await state.update_data(match_state=match_state)
                match_state['waiting_second_action'] = True
                await state.update_data(match_state=match_state)
                return
            else:  # Не угадал направление
                await send_photo_with_text(
                    callback.message,
                    'defense',
                    'save_fail.jpg',
                    "❌ Вратарь не угадал направление удара!"
                )
                await asyncio.sleep(2)
                
                # Шанс на спасение через защитников
                defender_save = random.random()
                if defender_save < 0.4:  # 40% шанс что защитники помогут
                    match_state['stats']['tackles'] += 1
                    await send_photo_with_text(
                        callback.message,
                        'defense',
                        'tackle_success.jpg',
                        "✅ Защитники подстраховали!\n- Мяч выбит в безопасную зону"
                    )
                    await continue_match(callback, match_state, state)
                elif defender_save < 0.7:  # 30% шанс что мяч уйдет на угловой
                    await send_photo_with_text(
                        callback.message,
                        'defense',
                        'deflect.jpg',
                        "↪️ Защитники заблокировали удар!\n- Мяч ушел на угловой"
                    )
                    await continue_match(callback, match_state, state)
        
        # Вторая фаза - действие с мячом после сейва
        elif action in ['kick', 'throw']:
            if not match_state.get('waiting_second_action'):
                await callback.answer("Сначала нужно спасти ворота!", show_alert=True)
                return
                
            if action == 'kick':
                await send_photo_with_text(
                    callback.message,
                    'goalkeeper',
                    'kick_start.jpg',
                    f"⚽ {match_state['current_team']} с мячом\n- Вратарь готовится выбить мяч"
                )
                await asyncio.sleep(2)
                
                if random.random() < 0.7:
                    await send_photo_with_text(
                        callback.message,
                        'goalkeeper',
                        'kick_success.jpg',
                        "✅ Мяч выбит!\n- Вратарь далеко выбил мяч в поле"
                    )
                else:
                    await send_photo_with_text(
                        callback.message,
                        'goalkeeper',
                        'kick_fail.jpg',
                        "❌ Неудачный выбив\n- Мяч перехвачен соперником"
                    )
                    await simulate_opponent_attack(callback, match_state)
            else:  # throw
                await send_photo_with_text(
                    callback.message,
                    'goalkeeper',
                    'throw_start.jpg',
                    f"🎯 {match_state['current_team']} с мячом\n- Вратарь готовится к выбросу мяча"
                )
                await asyncio.sleep(2)
                
                if random.random() < 0.8:
                    match_state['stats']['throws'] += 1
                    await send_photo_with_text(
                        callback.message,
                        'goalkeeper',
                        'throw_success.jpg',
                        "✅ Отличный выброс!\n- Вратарь точно выбросил мяч партнеру"
                    )
                else:
                    await send_photo_with_text(
                        callback.message,
                        'goalkeeper',
                        'throw_fail.jpg',
                        "❌ Неудачный выброс\n- Мяч перехвачен соперником"
                    )
                    await simulate_opponent_attack(callback, match_state)
            
            # Сбрасываем флаг ожидания второго действия
            match_state['waiting_second_action'] = False
            await state.update_data(match_state=match_state)
            await continue_match(callback, match_state, state)
    finally:
        # Сбрасываем флаг обработки в любом случае
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)

def get_defender_defense_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛡️ Отбор мяча", callback_data="defense_tackle")],
        [InlineKeyboardButton(text="🚫 Поставить блок", callback_data="defense_block")]
    ])

def get_defender_after_defense_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Отдать влево", callback_data="action_pass_left")],
        [InlineKeyboardButton(text="⚽ Выбить", callback_data="defense_clear")],
        [InlineKeyboardButton(text="➡️ Отдать вправо", callback_data="action_pass_right")]
    ])

async def handle_defender_tackle(callback: types.CallbackQuery, match_state, state: FSMContext):
    try:
        await send_photo_with_text(
            callback.message,
            'defense',
            'tackle_start.jpg',
            f"🛡️ {match_state['current_team']} в защите\n- Защитник готовится к отбору мяча"
        )
        await asyncio.sleep(3)
        
        if random.random() < 0.6:
            match_state['stats']['tackles'] += 1
            await send_photo_with_text(
                callback.message,
                'defense',
                'tackle_success.jpg',
                "✅ Отличный отбор!\n- Защитник успешно отобрал мяч\n\nВыберите следующее действие:"
            )
            # Сохраняем состояние успешного отбора
            match_state['defense_success'] = True
            await state.update_data(match_state=match_state)
            
            # Показываем клавиатуру с вариантами действий после отбора
            message = await callback.message.answer(
                "Что будете делать с мячом?",
                reply_markup=get_defender_after_defense_keyboard()
            )
            # Сохраняем ID сообщения с кнопками
            match_state['last_message_id'] = message.message_id
            await state.update_data(match_state=match_state)
        else:
            await send_photo_with_text(
                callback.message,
                'defense',
                'tackle_fail.jpg',
                "❌ Неудачный отбор\n- Соперник сохранил мяч"
            )
            await simulate_opponent_attack(callback, match_state)
            await continue_match(callback, match_state, state)
    except Exception as e:
        print(f"Error in handle_defender_tackle: {e}")
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)
        await callback.answer("Произошла ошибка. Попробуйте еще раз.", show_alert=True)
    finally:
        # Сбрасываем флаг обработки в любом случае
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)

async def handle_defender_block(callback: types.CallbackQuery, match_state, state: FSMContext):
    try:
        await send_photo_with_text(
            callback.message,
            'defense',
            'block_start.jpg',
            f"🚫 {match_state['current_team']} в защите\n- Защитник ставит блок"
        )
        await asyncio.sleep(3)
        
        if random.random() < 0.5:
            match_state['stats']['tackles'] += 1
            await send_photo_with_text(
                callback.message,
                'defense',
                'block_success.jpg',
                "✅ Отличный блок!\n- Защитник успешно заблокировал удар\n\nВыберите следующее действие:"
            )
            # Сохраняем состояние успешного блока
            match_state['defense_success'] = True
            await state.update_data(match_state=match_state)
            
            # Показываем клавиатуру с вариантами действий после блока
            message = await callback.message.answer(
                "Что будете делать с мячом?",
                reply_markup=get_defender_after_defense_keyboard()
            )
            # Сохраняем ID сообщения с кнопками
            match_state['last_message_id'] = message.message_id
            await state.update_data(match_state=match_state)
        else:                
            await send_photo_with_text(
                callback.message,
                'defense',
                'block_fail.jpg',
                "❌ Блок не удался\n- Соперник обыграл защитника"
            )
            await simulate_opponent_attack(callback, match_state)
            await continue_match(callback, match_state, state)
    except Exception as e:
        print(f"Error in handle_defender_block: {e}")
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)
        await callback.answer("Произошла ошибка. Попробуйте еще раз.", show_alert=True)
    finally:
        # Сбрасываем флаг обработки в любом случае
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)

async def handle_defender_pass_left(callback: types.CallbackQuery, match_state, state: FSMContext):
    try:
        await send_photo_with_text(
            callback.message,
            'pass',
            'left.jpg',
            f"⬅️ {match_state['current_team']} с мячом\n- Защитник отдает пас влево"
        )
        await asyncio.sleep(3)
        
        if random.random() < 0.7:
            match_state['stats']['assists'] += 1
            await send_photo_with_text(
                callback.message,
                'pass',
                'success.jpg',
                "✅ Отличный пас!\n- Партнер получил мяч в выгодной позиции"
            )
        else:
            await send_photo_with_text(
                callback.message,
                'pass',
                'intercept.jpg',
                "❌ Пас перехвачен\n- Соперник перехватил передачу"
            )
            await simulate_opponent_attack(callback, match_state)
        
        await continue_match(callback, match_state, state)
    finally:
        # Сбрасываем флаг обработки в любом случае
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)

async def handle_defender_pass_right(callback: types.CallbackQuery, match_state, state: FSMContext):
    try:
        await send_photo_with_text(
            callback.message,
            'pass',
            'right.jpg',
            f"➡️ {match_state['current_team']} с мячом\n- Защитник отдает пас вправо"
        )
        await asyncio.sleep(3)
        
        if random.random() < 0.7:
            match_state['stats']['assists'] += 1
            await send_photo_with_text(
                callback.message,
                'pass',
                'success.jpg',
                "✅ Отличный пас!\n- Партнер получил мяч в выгодной позиции"
            )
        else:
            await send_photo_with_text(
                callback.message,
                'pass',
                'intercept.jpg',
                "❌ Пас перехвачен\n- Соперник перехватил передачу"
            )
            await simulate_opponent_attack(callback, match_state)
        
        await continue_match(callback, match_state, state)
    finally:
        # Сбрасываем флаг обработки в любом случае
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)

async def handle_defender_clearance(callback: types.CallbackQuery, match_state, state: FSMContext):
    try:
        await send_photo_with_text(
            callback.message,
            'defense',
            'clear_start.jpg',
            f"⚽ {match_state['current_team']} в опасности\n- Защитник готовится выбить мяч"
        )
        await asyncio.sleep(3)
        
        if random.random() < 0.7:
            # Добавляем шанс случайного гола при выбивании мяча
            if random.random() < 0.05:  # 5% шанс случайного гола
                match_state['your_goals'] += 1
                match_state['stats']['goals'] += 1
                await send_photo_with_text(
                    callback.message,
                    'goals',
                    'goal.jpg',
                    f"⚽ ГООООЛ!\n- Невероятно! Защитник случайно забил гол! Счёт: {match_state['your_goals']}-{match_state['opponent_goals']}"
                )
            else:
                match_state['stats']['clearances'] += 1
                await send_photo_with_text(
                    callback.message,
                    'defense',
                    'clear_success.jpg',
                    "✅ Мяч выбит!\n- Защитник выбил мяч из опасной зоны"
                )
        else:
            await send_photo_with_text(
                callback.message,
                'defense',
                'clear_fail.jpg',
                "❌ Неудачный выбив\n- Мяч остался в опасной зоне"
            )
            await simulate_opponent_attack(callback, match_state)
        
        await continue_match(callback, match_state, state)
    finally:
        # Сбрасываем флаг обработки в любом случае
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)

async def handle_forward_shot(callback: types.CallbackQuery, match_state, state: FSMContext):
    try:
        await send_photo_with_text(
            callback.message,
            'shot',
            'start.jpg',
            f"⚽ {match_state['current_team']} с мячом\n- Нападающий готовится к удару"
        )
        await asyncio.sleep(3)
        
        if random.random() < 0.25:  # Уменьшаем шанс гола с 0.4 до 0.25
            match_state['your_goals'] += 1
            match_state['stats']['goals'] += 1
            await send_photo_with_text(
                callback.message,
                'goals',
                'goal.jpg',
                f"⚽ ГООООЛ!\n- Отличный удар! Счёт: {match_state['your_goals']}-{match_state['opponent_goals']}"
            )
        else:
            await send_photo_with_text(
                callback.message,
                'shot',
                'miss.jpg',
                "❌ Удар мимо\n- Вратарь соперника отразил удар"
            )
            await simulate_opponent_attack(callback, match_state)
        
        await continue_match(callback, match_state, state)
    finally:
        # Сбрасываем флаг обработки в любом случае
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)

async def handle_forward_pass(callback: types.CallbackQuery, match_state, state: FSMContext):
    try:
        await send_photo_with_text(
            callback.message,
            'pass',
            'start.jpg',
            f"🎯 {match_state['current_team']} с мячом\n- Нападающий ищет партнера для передачи"
        )
        await asyncio.sleep(3)
        
        if random.random() < 0.7:
            match_state['stats']['assists'] += 1
            await send_photo_with_text(
                callback.message,
                'pass',
                'success.jpg',
                "✅ Отличный пас!\n- Партнер получил мяч в выгодной позиции"
            )
            # После успешного паса предлагаем продолжить атаку
            await callback.message.answer(
                "Выберите следующее действие:",
                reply_markup=get_match_actions_keyboard('forward', is_second_phase=True)
            )
        else:
            await send_photo_with_text(
                callback.message,
                'pass',
                'intercept.jpg',
                "❌ Пас перехвачен\n- Соперник перехватил передачу"
            )
            await simulate_opponent_attack(callback, match_state)
            await continue_match(callback, match_state, state)
    finally:
        # Сбрасываем флаг обработки в любом случае
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)

async def handle_forward_dribble(callback: types.CallbackQuery, match_state, state: FSMContext):
    try:
        await send_photo_with_text(
            callback.message,
            'dribble',
            'start.jpg',
            f"🏃 {match_state['current_team']} с мячом\n- Нападающий начинает дриблинг"
        )
        await asyncio.sleep(3)
        
        if random.random() < 0.6:
            await send_photo_with_text(
                callback.message,
                'dribble',
                'success.jpg',
                "✅ Отличный дриблинг!\n- Нападающий обыграл защитника"
            )
            # После успешного дриблинга предлагаем продолжить атаку
            await callback.message.answer(
                "Выберите следующее действие:",
                reply_markup=get_match_actions_keyboard('forward', is_second_phase=True)
            )
        else:
            await send_photo_with_text(
                callback.message,
                'dribble',
                'fail.jpg',
                "❌ Потеря мяча\n- Защитник отобрал мяч"
            )
            await simulate_opponent_attack(callback, match_state)
            await continue_match(callback, match_state, state)
    finally:
        # Сбрасываем флаг обработки в любом случае
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)

async def continue_match(callback: types.CallbackQuery, match_state, state: FSMContext):
    # Увеличиваем минуту
    match_state['minute'] += random.randint(8, 12)
    
    if match_state['minute'] < 90:
        # Определяем, будет ли следующий момент атакой соперника для вратаря и защитника
        position = match_state['position']
        
        # Случайно выбираем, чья будет атака (40% шанс атаки своей команды)
        is_team_attack = random.random() < 0.4
        
        if position in ["Вратарь", "Защитник"]:
            if is_team_attack:
                # Симулируем атаку своей команды
                await simulate_team_attack(callback, match_state)
                message = (
                    f"⏱️ {match_state['minute']}' минута\n"
                    f"Счёт: {match_state['your_goals']} - {match_state['opponent_goals']}\n"
                    f"⚠️ {match_state['opponent_team']} начинает атаку!\n\n"
                    "Выберите действие:"
                )
            else:
                match_state['is_opponent_attack'] = True
                message = (
                    f"⏱️ {match_state['minute']}' минута\n"
                    f"Счёт: {match_state['your_goals']} - {match_state['opponent_goals']}\n"
                    f"⚠️ {match_state['opponent_team']} начинает атаку!\n\n"
                    "Выберите действие:"
                )
        else:
            message = (
                f"⏱️ {match_state['minute']}' минута\n"
                f"Счёт: {match_state['your_goals']} - {match_state['opponent_goals']}\n"
                f"- {'Последние минуты матча' if match_state['minute'] > 85 else 'Матч продолжается'}\n\n"
                "Выберите действие:"
            )
        
        # Создаем новую клавиатуру для следующего момента
        keyboard = get_match_actions_keyboard(position)
        
        # Отправляем сообщение и сохраняем его ID
        new_message = await callback.message.answer(message, reply_markup=keyboard)
        match_state['last_message_id'] = new_message.message_id
        await state.update_data(match_state=match_state)
    else:
        await finish_match(callback, state)

async def simulate_team_attack(callback: types.CallbackQuery, match_state):
    """Симуляция атаки своей команды"""
    attack_type = random.choices(
        ['dribble', 'shot', 'pass'],
        weights=[0.3, 0.4, 0.3]
    )[0]
    
    if attack_type == "shot":
        await send_photo_with_text(
            callback.message,
            'attack',
            'shot_start.jpg',
            f"⚽ {match_state['current_team']} атакует!\n- Партнер по команде готовится к удару"
        )
        await asyncio.sleep(2)
        
        if random.random() < 0.3:  # 30% шанс гола
            match_state['your_goals'] += 1
            await send_photo_with_text(
                callback.message,
                'goals',
                'goal.jpg',
                f"⚽ ГООООЛ!\n- Партнер по команде забивает! Счёт: {match_state['your_goals']}-{match_state['opponent_goals']}"
            )
        else:
            await send_photo_with_text(
                callback.message,
                'attack',
                'shot_miss.jpg',
                "❌ Мимо ворот\n- Удар партнера оказался неточным"
            )
    
    elif attack_type == "pass":
        await send_photo_with_text(
            callback.message,
            'attack',
            'pass_start.jpg',
            f"🎯 {match_state['current_team']} в атаке\n- Команда разыгрывает комбинацию"
        )
        await asyncio.sleep(2)
        
        if random.random() < 0.4:  # 40% шанс успешной комбинации
            match_state['your_goals'] += 1
            await send_photo_with_text(
                callback.message,
                'goals',
                'goal.jpg',
                f"⚽ ГООООЛ!\n- Красивая командная комбинация! Счёт: {match_state['your_goals']}-{match_state['opponent_goals']}"
            )
        else:
            await send_photo_with_text(
                callback.message,
                'attack',
                'pass_fail.jpg',
                "❌ Не получилось\n- Соперник прервал атаку"
            )
    
    else:  # dribble
        await send_photo_with_text(
            callback.message,
            'attack',
            'dribble_start.jpg',
            f"🏃 {match_state['current_team']} атакует\n- Партнер пытается обыграть защитника"
        )
        await asyncio.sleep(2)
        
        if random.random() < 0.35:  # 35% шанс успешной атаки
            match_state['your_goals'] += 1
            await send_photo_with_text(
                callback.message,
                'goals',
                'goal.jpg',
                f"⚽ ГООООЛ!\n- Индивидуальное мастерство! Счёт: {match_state['your_goals']}-{match_state['opponent_goals']}"
            )
        else:
            await send_photo_with_text(
                callback.message,
                'attack',
                'dribble_fail.jpg',
                "❌ Потеря мяча\n- Защитник соперника отобрал мяч"
            )

# Функция завершения матча
async def finish_match(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    match_state = data['match_state']

    player = await get_player(callback.from_user.id)
    matches = player.matches + 1  # matches
    wins = player.wins         # wins
    draws = player.draws        # draws
    losses = player.losses       # losses
    current_round = player.current_round + 1  # current_round
    
    if match_state['your_goals'] > match_state['opponent_goals']:
        wins += 1
        result = "победили"
        result_emoji = "🏆"
    elif match_state['your_goals'] < match_state['opponent_goals']:
        losses += 1
        result = "проиграли"
        result_emoji = "😔"
    else:
        draws += 1
        result = "сыграли вничью"
        result_emoji = "🤝"

    # Получаем новую дату после матча
    new_date = await advance_virtual_date(player)
    
    await update_player_stats(
        user_id=callback.from_user.id,
        matches=matches,
        wins=wins,
        draws=draws,
        losses=losses,
        goals=match_state['stats']['goals'],
        assists=match_state['stats']['assists'],
        saves=match_state['stats']['saves'],
        tackles=match_state['stats']['tackles'],
        current_round=current_round,
        last_match_date=new_date
    )
    
    stats = (f"{result_emoji} Матч завершен! Вы {result}!\n"
            f"🏆 Тур {match_state['round']} ФНЛ Серебро\n"
            f"📅 {new_date}\n\n"  # Показываем новую дату
            f"Итоговый счет: {match_state['your_goals']}-{match_state['opponent_goals']}\n\n"
            f"📊 Ваша статистика в матче:\n"
            f"Голы: {match_state['stats']['goals']}\n"
            f"Голевые передачи: {match_state['stats']['assists']}\n"
            f"Сейвы: {match_state['stats']['saves']}\n"
            f"Отборы: {match_state['stats']['tackles']}\n\n"
            f"📊 Общая статистика:\n"
            f"Матчи: {matches}\n"
            f"Победы: {wins}\n"
            f"Ничьи: {draws}\n"
            f"Поражения: {losses}")
    
    # Проверяем возможность перехода
    player = await get_player(callback.from_user.id)  # обновляем данные
    league, offers = get_transfer_offers(player)
    if offers:
        await callback.message.answer(
            "Вам поступили предложения от других клубов! Хотите перейти?",
            reply_markup=get_transfer_keyboard(offers, league)
        )
        # После выбора клуб обновится через отдельный callback
        await callback.answer()
        return
    # Создаем клавиатуру с кнопками "Статистика" и "Вернуться в меню"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="show_stats")],
        [InlineKeyboardButton(text="🏠 Вернуться в меню", callback_data="return_to_menu")]
    ])
    
    # Очищаем состояние матча перед отправкой сообщения
    await state.clear()  # Полностью очищаем все состояние
    await state.set_state(GameStates.playing)
    
    # Отправляем сообщение
    await callback.message.answer(stats, reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "show_stats")
async def show_stats_callback(callback: types.CallbackQuery, state: FSMContext):
    # Очищаем состояние матча
    await state.set_data({})
    await state.set_state(GameStates.playing)
    
    if not await check_subscription(callback.from_user.id):
        await callback.message.answer(
            "Для просмотра статистики необходимо подписаться на наш канал!",
            reply_markup=get_subscription_keyboard()
        )
        return
    
    player = await get_player(callback.from_user.id)
    if not player:
        await callback.message.answer(
            "Статистика не найдена. Начните игру с команды /start",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Получаем статистику с значениями по умолчанию, если какие-то данные отсутствуют
    name = player.name if player.name else "Игрок"
    position = player.position if player.position else "Не выбрана"
    club = player.club if player.club else "Не выбран"
    matches = player.matches if player.matches > 0 else 0
    wins = player.wins if player.wins > 0 else 0
    draws = player.draws if player.draws > 0 else 0
    losses = player.losses if player.losses > 0 else 0

    position_stats = ""
    if position == "Вратарь":
        saves = player.saves if player.saves > 0 else 0
        position_stats = f"Сейвы: {saves}\n"
    elif position == "Защитник":
        goals = player.goals if player.goals > 0 else 0
        assists = player.assists if player.assists > 0 else 0
        tackles = player.tackles if player.tackles > 0 else 0
        position_stats = f"Голы: {goals}\nГолевые передачи: {assists}\nОтборы: {tackles}\n"
    elif position == "Нападающий":
        goals = player.goals if player.goals > 0 else 0
        assists = player.assists if player.assists > 0 else 0
        position_stats = f"Голы: {goals}\nГолевые передачи: {assists}\n"
    
    stats = (f"📊 Статистика игрока {name} ({position})\n"
            f"Клуб: {club}\n\n"
            f"Матчи: {matches}\n"
            f"Победы: {wins}\n"
            f"Ничьи: {draws}\n"
            f"Поражения: {losses}\n\n"
            f"{position_stats}")
    
    await callback.message.answer(stats, reply_markup=get_main_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "return_to_menu")
async def return_to_menu_callback(callback: types.CallbackQuery, state: FSMContext):
    # Очищаем состояние матча
    await state.clear()
    await state.set_state(GameStates.playing)
    
    player = await get_player(callback.from_user.id)
    if player:
        welcome_text = (
            f"👋 Привет, {player.name}!\n\n"
            f"Вы играете за {player.club}\n"
            f"Позиция: {player.position}\n"
            f"{'✅ В стартовом составе' if player.is_in_squad else '❌ Не в заявке'}\n\n"
            "Добро пожаловать в футбольный симулятор!\n"
            "🏆 Побеждай в матчах\n"
            "⭐ Стань легендой футбола!"
        )
        with open("mbappe.png", "rb") as file:
            photo = BufferedInputFile(file.read(), filename="mbappe.png")
            await callback.message.answer_photo(
                photo,
                caption=welcome_text,
                reply_markup=get_main_keyboard()
            )
    await callback.answer()

async def simulate_opponent_attack(callback: types.CallbackQuery, match_state):
    attack_type = random.choices(
        ['dribble', 'shot', 'pass'],
        weights=[0.3, 0.4, 0.3]
    )[0]
    
    if attack_type == "dribble":
        await send_photo_with_text(
            callback.message,
            'opponent',
            'dribble_start.jpg',
            f"⚽ {match_state['opponent_team']} с мячом\n- Игрок соперника начинает дриблинг"
        )
        await asyncio.sleep(3)
        
        if random.random() < 0.6:
            await send_photo_with_text(
                callback.message,
                'opponent',
                'dribble_success.jpg',
                "❌ Соперник обыграл защитника\n- Игрок соперника успешно прошел защиту"
            )
            await asyncio.sleep(3)
            
            if random.random() < 0.5:
                await send_photo_with_text(
                    callback.message,
                    'opponent',
                    'shot_start.jpg',
                    "⚡ Подготовка к удару\n- Игрок соперника готовится нанести удар"
                )
                await asyncio.sleep(3)
                
                if random.random() < 0.4:
                    match_state['opponent_goals'] += 1
                    await send_photo_with_text(
                        callback.message,
                        'opponent',
                        'goal.jpg',
                        f"⚽ ГООООЛ соперника!\n- Отличный удар! Счёт: {match_state['your_goals']}-{match_state['opponent_goals']}"
                    )
                else:
                    match_state['stats']['saves'] += 1
                    await send_photo_with_text(
                        callback.message,
                        'defense',
                        'save.jpg',
                        "✅ Наш вратарь отразил удар\n- Вратарь совершил отличный сейв"
                    )
            else:
                match_state['stats']['tackles'] += 1
                await send_photo_with_text(
                    callback.message,
                    'defense',
                    'tackle.jpg',
                    "✅ Наш защитник успел подстраховать\n- Защитник не дал сопернику ударить"
                )
        else:
            match_state['stats']['tackles'] += 1
            await send_photo_with_text(
                callback.message,
                'defense',
                'tackle.jpg',
                "✅ Наш защитник отобрал мяч\n- Защитник успешно отобрал мяч"
            )
    
    elif attack_type == "shot":
        await send_photo_with_text(
            callback.message,
            'opponent',
            'shot_start.jpg',
            f"⚽ {match_state['opponent_team']} с мячом\n- Игрок соперника готовится к удару"
        )
        await asyncio.sleep(3)
        
        if random.random() < 0.3:
            match_state['opponent_goals'] += 1
            await send_photo_with_text(
                callback.message,
                'opponent',
                'goal.jpg',
                f"⚽ ГООООЛ соперника!\n- Отличный удар! Счёт: {match_state['your_goals']}-{match_state['opponent_goals']}"
            )
        else:
            match_state['stats']['saves'] += 1
            await send_photo_with_text(
                callback.message,
                'defense',
                'save.jpg',
                "✅ Наш вратарь отразил удар\n- Вратарь совершил отличный сейв"
            )
    
    elif attack_type == "pass":
        await send_photo_with_text(
            callback.message,
            'opponent',
            'pass_start.jpg',
            f"⚽ {match_state['opponent_team']} с мячом\n- Игрок соперника ищет партнера"
        )
        await asyncio.sleep(3)
        
        if random.random() < 0.6:
            await send_photo_with_text(
                callback.message,
                'opponent',
                'pass_success.jpg',
                "❌ Соперник отдал опасный пас\n- Партнер получил мяч в выгодной позиции"
            )
            await asyncio.sleep(3)
            
            if random.random() < 0.4:
                match_state['opponent_goals'] += 1
                await send_photo_with_text(
                    callback.message,
                    'opponent',
                    'goal.jpg',
                    f"⚽ ГООООЛ соперника!\n- Партнер реализовал момент! Счёт: {match_state['your_goals']}-{match_state['opponent_goals']}"
                )
            else:
                match_state['stats']['saves'] += 1
                await send_photo_with_text(
                    callback.message,
                    'defense',
                    'save.jpg',
                    "✅ Наш вратарь отразил удар\n- Вратарь совершил отличный сейв"
                )
        else:
            match_state['stats']['tackles'] += 1
            await send_photo_with_text(
                callback.message,
                'defense',
                'intercept.jpg',
                "✅ Наш защитник перехватил пас\n- Защитник успешно перехватил передачу"
            )

async def reset_player_stats(user_id):
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Player).where(Player.user_id == user_id).values(
                matches=0,
                wins=0,
                draws=0,
                losses=0,
                goals=0,
                assists=0,
                saves=0,
                tackles=0,
                current_round=1,
                last_match_date=SEASON_START_DATE
            )
        )
        await session.commit()

async def delete_player(user_id):
    """Удаляет игрока из базы данных"""
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(Player).where(Player.user_id == user_id)
        )
        await session.commit()

@dp.message(Command("reset_stats"))
async def cmd_reset_stats(message: types.Message, state: FSMContext):
    # Проверяем, не идет ли сейчас матч
    data = await state.get_data()
    if data.get('match_state'):
        await message.answer(
            "❌ Сейчас идет матч! Дождитесь его завершения.",
            reply_markup=get_main_keyboard()
        )
        return
    
    player = await get_player(message.from_user.id)
    if not player:
        await message.answer(
            "❌ Вы еще не создали своего игрока. Используйте команду /start",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Создаем клавиатуру с подтверждением
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, сбросить", callback_data="confirm_reset")],
        [InlineKeyboardButton(text="❌ Нет, отмена", callback_data="cancel_reset")]
    ])
    
    await message.answer(
        f"⚠️ Вы уверены, что хотите сбросить статистику?\n\n"
        f"Имя: {player.name}\n"
        f"Позиция: {player.position}\n"
        f"Клуб: {player.club}\n\n"
        f"Вся статистика будет обнулена, но имя, позиция и клуб останутся прежними.",
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data == "confirm_reset")
async def confirm_reset_callback(callback: types.CallbackQuery, state: FSMContext):
    await reset_player_stats(callback.from_user.id)
    await callback.message.edit_text(
        "✅ Статистика успешно сброшена!\n"
        "Используйте команду /start для начала новой карьеры."
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "cancel_reset")
async def cancel_reset_callback(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "❌ Сброс статистики отменен.\n"
        "Ваша статистика сохранена."
    )
    await callback.answer()

# 2. Функция для проверки и генерации предложений о переходе
TOP_SILVER = ["Текстильщик", "Сибирь", "Авангард-Курск"]
MID_GOLD = ["Волгарь", "Челябинск", "Родина-2", "Машук-КМВ", "Велес"]

def get_transfer_offers(player):
    # Получаем статистику игрока
    club = player.club
    matches = player.matches
    goals = player.goals
    assists = player.assists
    saves = player.saves
    tackles = player.tackles
    position = player.position
    offers = []
    # Переход из топ Серебра в середняк Золота
    if club in TOP_SILVER and matches >= 10 and (goals >= 5 or assists >= 5 or saves >= 40 or tackles >= 25):
        offers = random.sample(MID_GOLD, 2)
        return 'gold', offers
    # Переход внутри Серебра (вверх)
    elif club not in TOP_SILVER and matches >= 10 and (goals >= 5 or assists >= 5 or saves >= 5 or tackles >= 5):
        # Предлагаем топ-клубы Серебра, кроме текущего
        available = [c for c in TOP_SILVER if c != club]
        if available:
            offers = random.sample(available, min(2, len(available)))
            return 'silver', offers
    return None, []

# 3. Клавиатура для перехода

def get_transfer_keyboard(offers, league):
    keyboard = []
    for club in offers:
        keyboard.append([InlineKeyboardButton(
            text=f"{club} ({'Золото' if league == 'gold' else 'Серебро'})",
            callback_data=f"transfer_{league}_{club}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# 4. Callback для перехода
@dp.callback_query(lambda c: c.data.startswith('transfer_'))
async def transfer_callback(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split('_')
    league = parts[1]
    club = '_'.join(parts[2:])
    await update_player_club(callback.from_user.id, club)
    await callback.message.answer(f"Вы успешно перешли в клуб {club} ({'ФНЛ Золото' if league == 'gold' else 'ФНЛ Серебро'})! Поздравляем!", reply_markup=get_main_keyboard())
    await callback.answer()

@dp.message(Command("delete_player"))
async def cmd_delete_player(message: types.Message, state: FSMContext):
    # Проверяем, не идет ли сейчас матч
    data = await state.get_data()
    if data.get('match_state'):
        await message.answer(
            "❌ Сейчас идет матч! Дождитесь его завершения.",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Проверяем, существует ли игрок
    player = await get_player(message.from_user.id)
    if not player:
        await message.answer(
            "❌ Игрок не найден в базе данных.",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Создаем клавиатуру с подтверждением
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data="confirm_delete")],
        [InlineKeyboardButton(text="❌ Нет, отмена", callback_data="cancel_delete")]
    ])
    
    await message.answer(
        f"⚠️ Вы уверены, что хотите удалить игрока?\n\n"
        f"Имя: {player.name}\n"
        f"Позиция: {player.position}\n"
        f"Клуб: {player.club}\n\n"
        f"Вся статистика будет удалена без возможности восстановления.",
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data == "confirm_delete")
async def confirm_delete_callback(callback: types.CallbackQuery, state: FSMContext):
    await delete_player(callback.from_user.id)
    await callback.message.edit_text(
        "✅ Игрок успешно удален!\n"
        "Используйте команду /start для создания нового игрока."
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "cancel_delete")
async def cancel_delete_callback(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "❌ Удаление игрока отменено.\n"
        "Ваши данные сохранены."
    )
    await callback.answer()

async def main():
    await init_db()  # Инициализация базы данных при запуске
    await dp.start_polling(bot)

@dp.message(Command("admin_delete_player"))
async def cmd_admin_delete_player(message: types.Message, state: FSMContext):
    # Проверяем, является ли пользователь администратором
    if message.from_user.id != 5259325234:  # Только для вас
        await message.answer("❌ У вас нет прав для выполнения этой команды.")
        return
    
    # Получаем ID игрока из аргументов команды
    try:
        user_id = int(message.text.split()[1])
    except (IndexError, ValueError):
        await message.answer("❌ Укажите ID игрока: /admin_delete_player <ID>")
        return
    
    # Проверяем, существует ли игрок
    player = await get_player(user_id)
    if not player:
        await message.answer(f"❌ Игрок с ID {user_id} не найден в базе данных.")
        return
    
    # Удаляем игрока
    await delete_player(user_id)
    await message.answer(f"✅ Игрок {player.name} (ID: {user_id}) успешно удален из базы данных.")

if __name__ == "__main__":
    asyncio.run(main())
