import asyncio
import random
import time
import os
import logging
import json
from aiogram import Bot, Dispatcher, types
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
from dotenv import load_dotenv
from sqlalchemy import text

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

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
    personal_calendar = Column(String)  # JSON строка с календарем игрока

# --- Асинхронные функции работы с БД ---
async def get_player(user_id):
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Player).where(Player.user_id == user_id))
            player = result.scalar_one_or_none()
            if player:
                logger.debug(f"Получен игрок {player.name} (ID: {user_id})")
            else:
                logger.debug(f"Игрок с ID {user_id} не найден")
            return player
    except Exception as e:
        logger.error(f"Ошибка при получении игрока {user_id}: {e}")
        return None

async def create_player(user_id, name, position, club, start_date):
    try:
        # Предварительно выполняем миграцию БД для обеспечения наличия необходимых столбцов
        await migrate_database()
        
        # Создаем персональный календарь для игрока
        calendar = create_player_calendar(club)
        
        # Создаем основные поля игрока (без personal_calendar, на случай если миграция не сработала)
        player_data = {
            "user_id": user_id,
            "name": name, 
            "position": position, 
            "club": club,
            "last_match_date": start_date
        }
        
        async with AsyncSessionLocal() as session:
            # Пробуем добавить календарь, если миграция сработала
            try:
                player_data["personal_calendar"] = calendar
                player = Player(**player_data)
                session.add(player)
                await session.commit()
                logger.info(f"Создан новый игрок: {name} (ID: {user_id}, Позиция: {position}, Клуб: {club}, Дата начала: {start_date})")
            except Exception as e:
                # Если не удалось с календарем, пробуем без него
                if "personal_calendar" in str(e).lower():
                    logger.warning(f"Не удалось создать игрока с календарем, пробуем без календаря: {e}")
                    await session.rollback()
                    
                    # Удаляем поле personal_calendar из данных
                    player_data.pop("personal_calendar", None)
                    player = Player(**player_data)
                    session.add(player)
                    await session.commit()
                    logger.info(f"Создан новый игрок без календаря: {name} (ID: {user_id}, Позиция: {position}, Клуб: {club})")
                else:
                    # Другая ошибка, пробрасываем дальше
                    raise
    except Exception as e:
        logger.error(f"Ошибка при создании игрока {name} (ID: {user_id}): {e}")
        raise

async def update_player_stats(user_id, **kwargs):
    try:
        async with AsyncSessionLocal() as session:
            # Сначала получаем текущие данные игрока
            result = await session.execute(select(Player).where(Player.user_id == user_id))
            player = result.scalar_one_or_none()
            
            if not player:
                logger.warning(f"Попытка обновить несуществующего игрока {user_id}")
                return False
            
            # Создаем словарь с текущими статистическими данными
            current_stats = {
                "goals": player.goals or 0,
                "assists": player.assists or 0,
                "saves": player.saves or 0,
                "tackles": player.tackles or 0
            }
            
            # Обновляем данные игрока кумулятивно для статистических полей
            update_data = {}
            for key, value in kwargs.items():
                if key in ['goals', 'assists', 'saves', 'tackles']:
                    # Если переданное значение больше текущего, считаем что это новые данные для добавления
                    if value > current_stats.get(key, 0):
                        update_data[key] = current_stats.get(key, 0) + value
                    else:
                        # Иначе просто устанавливаем новое значение
                        update_data[key] = value
                else:
                    # Для не-статистических полей просто устанавливаем новое значение
                    update_data[key] = value
            
            # Выполняем обновление
            await session.execute(
                update(Player).where(Player.user_id == user_id).values(**update_data)
            )
            await session.commit()
            
            logger.debug(f"Обновлена статистика игрока {user_id}: {update_data}")
            return True
    except Exception as e:
        logger.error(f"Ошибка при обновлении статистики игрока {user_id}: {e}")
        return False

async def update_player_club(user_id, club):
    try:
        await update_player_stats(user_id, club=club)
        logger.info(f"Игрок {user_id} перешел в клуб {club}")
    except Exception as e:
        logger.error(f"Ошибка при обновлении клуба игрока {user_id}: {e}")
        raise

async def update_player_squad_status(user_id, is_in_squad):
    try:
        await update_player_stats(user_id, is_in_squad=is_in_squad)
        logger.info(f"Игрок {user_id} {'включен в' if is_in_squad else 'исключен из'} заявки")
    except Exception as e:
        logger.error(f"Ошибка при обновлении статуса заявки игрока {user_id}: {e}")
        raise

# --- Инициализция базы ---
async def migrate_database():
    """Миграция базы данных: добавление столбца personal_calendar, если его не существует"""
    try:
        logger.info("Проверка и миграция базы данных...")
        
        # Проверяем, существует ли столбец personal_calendar в таблице players
        async with engine.begin() as conn:
            # Для PostgreSQL
            check_query = text("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'players' AND column_name = 'personal_calendar';
            """)
            result = await conn.execute(check_query)
            column_exists = bool(result.scalar())
            
            if not column_exists:
                logger.info("Столбец personal_calendar не найден, добавляем...")
                # Добавляем столбец personal_calendar
                alter_query = text("""
                ALTER TABLE players ADD COLUMN personal_calendar TEXT;
                """)
                await conn.execute(alter_query)
                
                # Явно ждем завершения транзакции
                await conn.commit()
                
                # Проверяем еще раз, что столбец действительно добавлен
                result = await conn.execute(check_query)
                column_exists = bool(result.scalar())
                
                if column_exists:
                    logger.info("Столбец personal_calendar успешно добавлен в таблицу players")
                else:
                    logger.error("Не удалось добавить столбец personal_calendar!")
                    raise Exception("Не удалось добавить столбец personal_calendar!")
            else:
                logger.info("Столбец personal_calendar уже существует")
        
        logger.info("Миграция базы данных успешно завершена")
        return True
    except Exception as e:
        logger.error(f"Ошибка при миграции базы данных: {e}")
        return False

async def init_db():
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            logger.info("База данных успешно инициализирована")
        
        # Выполняем миграцию существующей базы данных
        await migrate_database()
    except Exception as e:
        logger.error(f"Ошибка при инициализации базы данных: {e}")
        raise

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
WINTER_BREAK_END = 3    # Март (конец февраля - возобновление в марте)
DAYS_BETWEEN_MATCHES = 7  # Количество дней между матчами
SEASON_START_DATE = "01.09.2025"  # Начало сезона в формате DD.MM.YYYY

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
        [InlineKeyboardButton(text="📊 Статистика", callback_data="show_stats")],
        [InlineKeyboardButton(text="📅 Календарь", callback_data="show_calendar")]
    ])

# Функция для создания клавиатуры для возврата в главное меню
def get_main_menu_keyboard():
    """Возвращает клавиатуру для возврата в главное меню"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Вернуться в меню", callback_data="return_to_menu")]
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
    """Отправляет фото с описанием с обработкой возможных ошибок"""
    try:
        photo_path = os.path.join('images', folder, filename)
        if os.path.exists(photo_path):
            with open(photo_path, 'rb') as file:
                photo = BufferedInputFile(file.read(), filename=filename)
                await message.answer_photo(photo, caption=text)
        else:
            await message.answer(text)
    except Exception as e:
        logger.error(f"Ошибка при отправке фото {folder}/{filename}: {e}")
        # Если не удалось отправить фото, пробуем хотя бы текст
        try:
            await message.answer(f"{text}\n(Изображение недоступно)")
        except Exception as inner_e:
            logger.error(f"Дополнительная ошибка при отправке текста: {inner_e}")

# Улучшенная функция ожидания с защитой от ошибок
async def safe_sleep(seconds):
    """Безопасное ожидание, которое не вызывает блокировку событийного цикла"""
    try:
        # Используем короткие интервалы для возможности прерывания
        iterations = int(seconds * 2)
        for _ in range(iterations):
            await asyncio.sleep(0.5)
    except Exception as e:
        logger.debug(f"Ошибка во время ожидания: {e}")
        # Минимальная пауза в случае ошибки
        await asyncio.sleep(0.1)

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    logger.info(f"Пользователь {message.from_user.id} запустил команду /start")
    
    # Проверяем, не идет ли сейчас матч
    data = await state.get_data()
    if data.get('match_state'):
        logger.warning(f"Пользователь {message.from_user.id} попытался использовать /start во время матча")
        await message.answer(
            "❌ Сейчас идет матч! Дождитесь его завершения."
        )
        return

    if not await check_subscription(message.from_user.id):
        logger.info(f"Пользователь {message.from_user.id} не подписан на канал")
        await message.answer(
            "Для использования бота необходимо подписаться на наш канал!",
            reply_markup=get_subscription_keyboard()
        )
        return
    
    player = await get_player(message.from_user.id)
    if player:
        logger.info(f"Существующий игрок {player.name} (ID: {message.from_user.id}) вошел в игру")
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
        logger.info(f"Новый игрок (ID: {message.from_user.id}) начал регистрацию")
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
        try:
            await callback.answer()
        except Exception as e:
            logger.debug(f"Не удалось ответить на callback: {e}")
    else:
        await callback.answer("Вы все еще не подписаны на канал!", show_alert=True)

@dp.message(GameStates.waiting_name)
async def process_name(message: types.Message, state: FSMContext):
    logger.info(f"Пользователь {message.from_user.id} ввел имя: {message.text}")
    
    if not await check_subscription(message.from_user.id):
        logger.warning(f"Пользователь {message.from_user.id} не подписан на канал при вводе имени")
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
    
    logger.info(f"Игрок {name} (ID: {callback.from_user.id}) выбрал позицию: {position}")
    
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
    try:
        await callback.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback: {e}")

def get_initial_player_date():
    """Определяет начальную дату для нового игрока"""
    # Всегда возвращаем фиксированную дату начала сезона в формате DD.MM.YYYY
    return SEASON_START_DATE

@dp.callback_query(lambda c: c.data.startswith('choose_club_'), GameStates.waiting_club_choice)
async def process_club_choice(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        # Получаем название клуба из callback data
        club = callback_query.data.replace('choose_club_', '')
        user_id = callback_query.from_user.id
        
        # Получаем данные из состояния
        data = await state.get_data()
        name = data.get('name')
        position = data.get('position')
        
        # Определяем начальную дату для игрока
        start_date = get_initial_player_date()
        
        # Создаем игрока
        await create_player(user_id, name, position, club, start_date)
        
        # Очищаем состояние и устанавливаем режим игры
        await state.clear()
        await state.set_state(GameStates.playing)
        
        # Отправляем приветственное сообщение
        welcome_text = (
            f"Добро пожаловать в футбольный симулятор, {name}!\n\n"
            f"Вы зарегистрированы как {position} в клубе {club}.\n"
            f"Дата начала карьеры: {datetime.strptime(start_date, '%d.%m.%Y').strftime('%d.%m.%Y')}\n\n"
            "Используйте кнопку 'Играть матч' для начала игры или 'Статистика' для просмотра своих достижений."
        )
        
        await callback_query.message.edit_text(
            welcome_text,
            reply_markup=None
        )
        
        # Отправляем главное меню отдельным сообщением
        await callback_query.message.answer(
            "Выберите действие:",
            reply_markup=get_main_keyboard()
        )
        logger.info(f"Игрок {name} (ID: {user_id}) успешно зарегистрирован в клубе {club}")
        
    except Exception as e:
        logger.error(f"Ошибка при выборе клуба: {e}")
        await callback_query.message.edit_text(
            "Произошла ошибка при регистрации. Пожалуйста, попробуйте снова через /start",
            reply_markup=None
        )
    try:
        await callback_query.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback: {e}")

async def get_virtual_date(player):
    """Получает виртуальную дату игрока в формате DD.MM.YYYY"""
    try:
        # Проверяем формат даты: может быть YYYY-MM-DD или DD.MM.YYYY
        if "-" in player.last_match_date:
            # Если дата в формате YYYY-MM-DD
            date = datetime.strptime(player.last_match_date, "%Y-%m-%d")
        elif "." in player.last_match_date:
            # Если дата уже в формате DD.MM.YYYY
            date = datetime.strptime(player.last_match_date, "%d.%m.%Y")
        else:
            # Неизвестный формат
            logger.error(f"Неизвестный формат даты: {player.last_match_date}")
            return "01.09.2025"
            
        # Возвращаем в формате DD.MM.YYYY
        return date.strftime("%d.%m.%Y")
    except Exception as e:
        logger.error(f"Ошибка при получении виртуальной даты: {e}")
        # В случае ошибки возвращаем дату начала сезона
        return "01.09.2025"

def is_season_active(virtual_date):
    """Проверяет, идет ли сейчас сезон в виртуальном времени"""
    try:
        # Если получили уже datetime объект
        if isinstance(virtual_date, datetime):
            date = virtual_date
        else:
            # Парсим дату из формата DD.MM.YYYY
            date = datetime.strptime(virtual_date, "%d.%m.%Y")
        
        current_month = date.month
        return (9 <= current_month <= 12) or (1 <= current_month <= 5)
    except Exception as e:
        logger.error(f"Ошибка при проверке активности сезона: {e}")
        return False

def is_winter_break(virtual_date):
    """Проверяет, идет ли сейчас зимний перерыв в виртуальном времени"""
    try:
        # Если получили уже datetime объект
        if isinstance(virtual_date, datetime):
            date = virtual_date
        else:
            # Парсим дату с учетом возможных форматов
            try:
                # Пробуем формат DD.MM.YYYY
                date = datetime.strptime(virtual_date, "%d.%m.%Y")
            except ValueError:
                # Если не получилось, пробуем формат YYYY-MM-DD
                date = datetime.strptime(virtual_date, "%Y-%m-%d")
        
        current_month = date.month
        # Зимний перерыв с декабря по февралю включительно
        return current_month == WINTER_BREAK_START or (current_month >= 1 and current_month < WINTER_BREAK_END)
    except Exception as e:
        logger.error(f"Ошибка при проверке зимнего перерыва: {e}")
        return False

async def can_play_match(player, in_day=False):
    """Проверяет, может ли игрок сыграть матч, с учетом текущей виртуальной даты и зимнего перерыва"""
    try:
        # Проверяем, не находится ли текущая дата в зимнем перерыве
        if is_winter_break(player.last_match_date):
            return False, "Зимний перерыв. Матчи не проводятся до марта! ⛄️"
        
        # Проверяем, есть ли матч в текущем туре
        current_round = player.current_round if player.matches > 0 else 1
        opponent = await get_opponent_by_round(player, current_round)
        
        # Если матча нет и мы дошли до конца календаря, значит сезон закончен
        if not opponent and current_round > 18:
            # Сезон закончен, сбрасываем на начало нового сезона
            await start_new_season(player)
            return True, "Начинается новый сезон! Приготовьтесь к первому матчу! 🏆"
        
        # Если в этот день игрок уже сыграл матч (для защиты от повторов)
        if in_day and player.last_match_day == datetime.now().strftime("%Y-%m-%d"):
            return False, "Вы уже сыграли матч сегодня. Следующий матч будет доступен завтра! ⏰"
        
        return True, ""
    except Exception as e:
        logger.error(f"Ошибка при проверке возможности сыграть матч: {e}")
        return False, "Произошла ошибка. Попробуйте позже."

async def advance_virtual_date(player):
    """Увеличивает виртуальную дату на 7 дней, с учетом зимнего перерыва и смены года"""
    try:
        # Определяем формат даты и парсим текущую дату
        if "-" in player.last_match_date:
            # Формат YYYY-MM-DD
            current_date = datetime.strptime(player.last_match_date, "%Y-%m-%d")
        elif "." in player.last_match_date:
            # Формат DD.MM.YYYY
            current_date = datetime.strptime(player.last_match_date, "%d.%m.%Y")
        else:
            # Неизвестный формат, используем дату начала сезона
            logger.error(f"Неизвестный формат даты: {player.last_match_date}")
            current_date = datetime.strptime(SEASON_START_DATE, "%d.%m.%Y")
        
        # Добавляем 7 дней
        new_date = current_date + timedelta(days=DAYS_BETWEEN_MATCHES)
        
        # Проверяем, если переходим из одного года в другой
        if new_date.year > current_date.year:
            logger.info(f"Смена года: {current_date.year} -> {new_date.year}")
        
        # Проверяем, не наступил ли зимний перерыв
        if not is_winter_break(current_date) and is_winter_break(new_date):
            logger.info(f"Наступил зимний перерыв для игрока {player.name}")
            # Если переходим на зимний перерыв, сдвигаем дату сразу после него
            if new_date.month == WINTER_BREAK_START:  # Декабрь
                # Переходим на март следующего года (конец зимнего перерыва)
                new_date = datetime(new_date.year + 1, WINTER_BREAK_END, 1)
            else:
                # Если уже в начале года, просто переходим на март
                new_date = datetime(new_date.year, WINTER_BREAK_END, 1)
        
        # Проверяем, не закончился ли сезон
        if (current_date.month < SEASON_END_MONTH or 
            (current_date.month == SEASON_END_MONTH and current_date.day < 25)) and \
           (new_date.month > SEASON_END_MONTH or 
            (new_date.month == SEASON_END_MONTH and new_date.day >= 25)):
            logger.info(f"Сезон закончился для игрока {player.name}")
            
            # Генерируем предложения о переходе
            await generate_transfer_offers(player)
            
            # Переходим на следующий сезон (сентябрь)
            if current_date.month == SEASON_END_MONTH:  # Май
                # Переходим сразу на сентябрь того же года (начало нового сезона)
                new_date = datetime(new_date.year, SEASON_START_MONTH, 1)
                # Создаем новый календарь для следующего сезона
                await start_new_season(player)
        
        # Форматируем новую дату для сохранения
        virtual_date = new_date.strftime("%d.%m.%Y")
        
        # Обновляем информацию игрока
        await update_player_stats(
            user_id=player.user_id,
            last_match_date=virtual_date
        )
        
        logger.info(f"Обновлена виртуальная дата для игрока {player.name}: {virtual_date}")
        return virtual_date
    except Exception as e:
        logger.error(f"Ошибка при обновлении виртуальной даты: {e}")
        return player.last_match_date

async def get_opponent_by_round(player, current_round):
    """Получает соперника по текущему туру из персонального календаря игрока"""
    try:
        # Проверяем наличие календаря
        if not hasattr(player, 'personal_calendar') or not player.personal_calendar:
            logger.warning(f"У игрока {player.name} (ID: {player.user_id}) отсутствует календарь, создаем новый")
            # Создаем новый календарь
            calendar_json = create_player_calendar(player.club)
            # Сохраняем календарь в базу
            await update_player_stats(
                user_id=player.user_id,
                personal_calendar=calendar_json
            )
            # Используем обычного соперника до следующего обновления
            return get_opponent_by_round_default(player.club, current_round)
        
        # Парсим JSON календарь
        calendar = json.loads(player.personal_calendar)
        
        # Проверяем, не вышли ли за пределы календаря (18 туров)
        if current_round > 18:
            logger.warning(f"Запрошен тур {current_round}, но в календаре максимум 18 туров")
            # Если сезон закончился, возвращаем None, чтобы можно было начать новый сезон
            return None
            
        # Ищем матч текущего тура
        for match in calendar:
            if match["round"] == current_round:
                logger.info(f"Матч тура {current_round} найден в календаре игрока {player.name}: {match}")
                return match["opponent"]
        
        # Если матч не найден, выводим предупреждение
        logger.warning(f"В календаре игрока {player.name} не найден матч для тура {current_round}")
        
        # Пытаемся подобрать случайного соперника
        random_opponent = random.choice(list(FNL_SILVER_CLUBS.keys()))
        while random_opponent == player.club:
            random_opponent = random.choice(list(FNL_SILVER_CLUBS.keys()))
        
        logger.warning(f"Для клуба {player.club} в туре {current_round} не найден соперник в календаре - выбран случайный клуб {random_opponent}")
        return random_opponent
    except Exception as e:
        logger.error(f"Ошибка при получении соперника из календаря: {e}")
        # В случае ошибки используем обычный способ
        return get_opponent_by_round_default(player.club, current_round)

# Функция для генерации предложений о переходе
async def generate_transfer_offers(player):
    """Генерирует случайные предложения о переходе в другие клубы в конце сезона"""
    try:
        # Генерируем предложения только в конце сезона (если май)
        current_date = datetime.strptime(player.last_match_date, "%d.%m.%Y")
        if current_date.month != SEASON_END_MONTH:
            return
            
        logger.info(f"Игроку {player.name} (ID: {player.user_id}) поступили предложения о переходе")
        
        # Выбираем 3 случайных клуба, кроме текущего
        available_clubs = [club for club in FNL_SILVER_CLUBS.keys() if club != player.club]
        if len(available_clubs) < 3:
            offer_clubs = available_clubs
        else:
            offer_clubs = random.sample(available_clubs, 3)
        
        # Создаем предложения
        offers = []
        for club in offer_clubs:
            # Случайная зарплата, немного выше текущей
            salary_increase = random.uniform(1.1, 1.5)
            new_salary = int(player.salary * salary_increase)
            
            offers.append({
                "club": club,
                "salary": new_salary,
                "stars": FNL_SILVER_CLUBS.get(club, 1)  # Рейтинг клуба
            })
        
        # Сохраняем предложения в базе данных
        await update_player_stats(
            user_id=player.user_id,
            transfer_offers=json.dumps(offers)
        )
        
        return offers
    except Exception as e:
        logger.error(f"Ошибка при генерации предложений о переходе: {e}")
        return []

# Создаем календарь матчей
def create_calendar():
    """
    Создает календарь из 18 туров (9 в первом круге + 9 во втором)
    В каждом туре каждая команда играет ровно один матч
    """
    all_clubs = list(FNL_SILVER_CLUBS.keys())
    total_clubs = len(all_clubs)
    
    # Для кругового турнира нужно четное количество команд
    if total_clubs % 2 != 0:
        all_clubs.append("Выходной")
        total_clubs += 1
    
    # В одном круге команда играет против каждой другой команды по одному разу
    # Всего туров в одном круге: (n-1), где n - количество команд
    rounds_per_circle = total_clubs - 1
    
    # Общее количество туров (два круга)
    total_rounds = 18
    
    # Если количество туров в круговом турнире больше 9, 
    # значит у нас более 10 команд, и мы должны выбрать только 9 туров на круг
    if rounds_per_circle > 9:
        # Выбираем первые 9 туров для первого круга
        rounds_per_circle = 9
    
    # Календарь будет списком кортежей (home_team, away_team, round_number)
    calendar = []
    
    # Алгоритм создания кругового турнира (алгоритм Бержа)
    # Фиксируем первую команду и вращаем остальные
    teams = all_clubs.copy()
    
    for round_num in range(1, rounds_per_circle + 1):
        round_matches = []
        
        # Матчи в этом туре
        for i in range(total_clubs // 2):
            home_team = teams[i]
            away_team = teams[total_clubs - 1 - i]
            
            # Пропускаем матчи с фиктивной командой "Выходной"
            if home_team != "Выходной" and away_team != "Выходной":
                # Нечетные туры - первая команда дома, четные - в гостях
                if round_num % 2 == 1:
                    round_matches.append((home_team, away_team, round_num))
                else:
                    round_matches.append((away_team, home_team, round_num))
        
        # Добавляем матчи этого тура в общий календарь
        calendar.extend(round_matches)
        
        # Вращение: первый элемент фиксируется, остальные сдвигаются
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]
    
    # Второй круг (меняем домашние и гостевые команды)
    first_round_calendar = calendar.copy()
    for home, away, round_num in first_round_calendar:
        # Второй круг начинается после первого (round_num + rounds_per_circle)
        calendar.append((away, home, round_num + rounds_per_circle))
    
    # Сортируем по номеру тура для удобства
    calendar.sort(key=lambda match: match[2])
    
    return calendar

# Глобальный календарь матчей
MATCH_CALENDAR = create_calendar()

# Функция для получения соперника по текущему туру
def get_opponent_by_round_default(player_club, current_round):
    # Проверяем, не вышли ли за пределы календаря
    if current_round > len(MATCH_CALENDAR):
        # Если турнир закончен, начинаем новый
        current_round = 1
        
    # Получаем матч текущего тура
    match = MATCH_CALENDAR[current_round - 1]
    
    # Проверяем, участвует ли клуб игрока в матче
    if match[0] == player_club:
        logger.info(f"Клуб {player_club} играет в туре {current_round} против {match[1]}")
        return match[1]  # Соперник - вторая команда
    elif match[1] == player_club:
        logger.info(f"Клуб {player_club} играет в туре {current_round} против {match[0]}")
        return match[0]  # Соперник - первая команда
    
    # Если клуб игрока не участвует в этом туре, ищем следующий матч
    for i in range(current_round, len(MATCH_CALENDAR)):
        match = MATCH_CALENDAR[i]
        if match[0] == player_club:
            logger.info(f"Для клуба {player_club} в туре {current_round} найден соперник {match[1]} в будущем туре {i+1}")
            return match[1]
        elif match[1] == player_club:
            logger.info(f"Для клуба {player_club} в туре {current_round} найден соперник {match[0]} в будущем туре {i+1}")
            return match[0]
    
    # Если в этом сезоне больше нет матчей, ищем в начале календаря
    for i in range(current_round - 1):
        match = MATCH_CALENDAR[i]
        if match[0] == player_club:
            logger.info(f"Для клуба {player_club} в туре {current_round} найден соперник {match[1]} в прошлом туре {i+1}")
            return match[1]
        elif match[1] == player_club:
            logger.info(f"Для клуба {player_club} в туре {current_round} найден соперник {match[0]} в прошлом туре {i+1}")
            return match[0]
    
    # Если соперник все еще не найден, возвращаем случайную команду (кроме клуба игрока)
    all_clubs = list(FNL_SILVER_CLUBS.keys())
    available_clubs = [club for club in all_clubs if club != player_club]
    if available_clubs:
        random_opponent = random.choice(available_clubs)
        logger.warning(f"Для клуба {player_club} в туре {current_round} не найден соперник в календаре - выбран случайный клуб {random_opponent}")
        return random_opponent
        
    logger.error(f"Для клуба {player_club} в туре {current_round} не удалось найти соперника!")
    return None

@dp.callback_query(lambda c: c.data == "play_match")
async def play_match_callback(callback: types.CallbackQuery, state: FSMContext):
    """Обработчик начала матча"""
    try:
        logger.info(f"Пользователь {callback.from_user.id} начал матч")
        
        # Получаем данные игрока
        player = await get_player(callback.from_user.id)
        if not player:
            await callback.message.answer(
                "Вы не зарегистрированы. Используйте команду /start для создания игрока."
            )
            await callback.answer()
            return
            
        # Проверяем, может ли игрок сейчас сыграть матч
        can_play, message = await can_play_match(player)
        if not can_play:
            await callback.message.answer(message)
            await callback.answer()
            return
        
        # Определяем текущий тур игрока
        current_round = player.current_round if player.matches > 0 else 1
        
        # Получаем соперника из персонального календаря
        opponent = await get_opponent_by_round(player, current_round)
        
        # Если оппонент не найден (конец сезона), перенаправляем на новый сезон
        if not opponent:
            # Создаем новый сезон
            success = await start_new_season(player)
            if success:
                # Получаем обновленные данные игрока
                player = await get_player(callback.from_user.id)
                current_round = 1
                opponent = await get_opponent_by_round(player, current_round)
            else:
                await callback.message.answer("Ошибка при создании нового сезона. Пожалуйста, попробуйте снова.")
                await callback.answer()
                return
        
        # Проверяем, домашний или выездной матч
        is_home = True  # По умолчанию домашний
        
        # Пытаемся получить информацию о домашнем/выездном из календаря
        try:
            calendar = json.loads(player.personal_calendar)
            for match in calendar:
                if match["round"] == current_round:
                    is_home = match["is_home"]
                    break
        except Exception as e:
            logger.error(f"Ошибка при получении информации о домашнем/выездном матче: {e}")
        
        # Определяем текущую и следующую команды
        if is_home:
            current_team = player.club
            opponent_team = opponent
        else:
            current_team = opponent
            opponent_team = player.club
        
        # Логируем начало матча
        logger.info(f"Начался матч: {player.club} vs {opponent} (Тур {current_round})")
        
        # Сохраняем состояние матча
        match_state = {
            "your_goals": 0,
            "opponent_goals": 0,
            "current_team": current_team,
            "opponent_team": opponent_team,
            "current_round": current_round,
            "is_home": is_home,
            "last_message_id": None,
            "stats": {
                "goals": 0,
                "assists": 0,
                "saves": 0,
                "tackles": 0,
                "fouls": 0,
                "passes": 0,
                "interceptions": 0
            },
            "position": player.position,
            "virtual_date": player.last_match_date
        }
        
        await state.update_data(match_state=match_state)
        
        # Начинаем матч
        message = await callback.message.answer(
            f"🏆 <b>Тур {current_round}</b>\n"
            f"{'🏠' if is_home else '🚌'} <b>{current_team}</b> vs <b>{opponent_team}</b>\n\n"
            "Матч начинается! Приготовьтесь к игре...",
            parse_mode="HTML"
        )
        
        # Запоминаем ID этого сообщения
        match_state["last_message_id"] = message.message_id
        await state.update_data(match_state=match_state)
        
        # Запускаем игровой процесс
        await start_match(message, match_state, state)
        
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка при начале матча: {e}")
        await callback.message.answer("Произошла ошибка при начале матча. Пожалуйста, попробуйте снова.")
        await callback.answer()

@dp.callback_query(lambda c: c.data.startswith('action_'))
async def handle_action(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    match_state = data.get('match_state')
    
    if not match_state:
        await callback.message.answer(
            "Матч не начат или уже завершен. Нажмите 'Играть матч' для начала нового матча."
        )
        try:
            await callback.answer("Матч не активен", show_alert=True)
        except Exception as e:
            logger.debug(f"Не удалось ответить на callback: {e}")
        return
    
    # Проверяем, завершен ли матч
    if match_state.get('match_finished', False):
        await callback.message.answer(
            "Матч уже завершен. Нажмите 'Играть матч' для начала нового матча."
        )
        try:
            await callback.answer("Матч уже завершен", show_alert=True)
        except Exception as e:
            logger.debug(f"Не удалось ответить на callback: {e}")
        return
    
    # Проверяем, что кнопка из последнего сообщения
    if callback.message.message_id != match_state.get('last_message_id'):
        try:
            await callback.answer(
                "Используйте кнопки из последнего сообщения ⬇️",
                show_alert=True
            )
        except Exception as e:
            logger.debug(f"Не удалось ответить на callback: {e}")
        return
    
    # Проверяем, не превышено ли максимальное количество действий в матче
    MAX_ACTIONS_PER_MATCH = 50  # Максимальное количество действий в одном матче
    actions_count = match_state.get('actions_count', 0)
    
    if actions_count >= MAX_ACTIONS_PER_MATCH:
        logger.warning(f"Пользователь {callback.from_user.id} превысил лимит действий в матче ({actions_count})")
        
        # Автоматически завершаем матч
        await callback.message.answer(
            "Достигнут лимит действий в матче. Матч будет автоматически завершен.",
            reply_markup=None
        )
        
        # Завершаем матч
        await finish_match(callback, state)
        return
    
    # Увеличиваем счетчик действий
    match_state['actions_count'] = actions_count + 1
    await state.update_data(match_state=match_state)
    
    # Проверяем, не обрабатывается ли уже момент
    if match_state.get('is_processing', False):
        try:
            await callback.answer("Дождитесь завершения текущего момента", show_alert=True)
        except Exception as e:
            logger.debug(f"Не удалось ответить на callback: {e}")
        return
    
    # Устанавливаем флаг обработки момента
    match_state['is_processing'] = True
    await state.update_data(match_state=match_state)
    
    try:
        action = callback.data.split('_')[1]
        position = match_state['position']
        
        # Безопасный ответ на callback
        try:
            await callback.answer()
        except Exception as e:
            logger.debug(f"Не удалось ответить на callback: {e}")
        
        if position == "Вратарь":
            await handle_goalkeeper_save(callback, match_state, state)
        elif position == "Защитник":
            if action == "tackle":
                await handle_defender_tackle(callback, match_state, state)
            elif action == "block":
                await handle_defender_block(callback, match_state, state)
            elif action == "clear":
                await handle_defender_clearance(callback, match_state, state)
            elif action == "pass_left":
                await handle_defender_pass_left(callback, match_state, state)
            elif action == "pass_right":
                await handle_defender_pass_right(callback, match_state, state)
        else:  # Нападающий
            if action == "shot":
                await handle_forward_shot(callback, match_state, state)
            elif action == "pass":
                await handle_forward_pass(callback, match_state, state)
            elif action == "dribble":
                await handle_forward_dribble(callback, match_state, state)
    except Exception as e:
        logger.error(f"Ошибка при обработке действия: {e}")
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)
        try:
            await callback.answer("Произошла ошибка. Попробуйте еще раз.", show_alert=True)
        except Exception as err:
            logger.debug(f"Не удалось ответить на callback после ошибки: {err}")
    finally:
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)

@dp.callback_query(lambda c: c.data.startswith('defense_'))
async def handle_defense_action(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    match_state = data.get('match_state')
    
    if not match_state:
        await callback.message.answer(
            "Матч не начат или уже завершен. Нажмите 'Играть матч' для начала нового матча."
        )
        try:
            await callback.answer("Матч не активен", show_alert=True)
        except Exception as e:
            logger.debug(f"Не удалось ответить на callback: {e}")
        return
    
    # Проверяем, завершен ли матч
    if match_state.get('match_finished', False):
        await callback.message.answer(
            "Матч уже завершен. Нажмите 'Играть матч' для начала нового матча."
        )
        try:
            await callback.answer("Матч уже завершен", show_alert=True)
        except Exception as e:
            logger.debug(f"Не удалось ответить на callback: {e}")
        return
    
    # Проверяем, что кнопка из последнего сообщения
    if callback.message.message_id != match_state.get('last_message_id'):
        try:
            await callback.answer(
                "Используйте кнопки из последнего сообщения ⬇️",
                show_alert=True
            )
        except Exception as e:
            logger.debug(f"Не удалось ответить на callback: {e}")
        return
    
    # Проверяем, не превышено ли максимальное количество действий в матче
    MAX_ACTIONS_PER_MATCH = 50  # Максимальное количество действий в одном матче
    actions_count = match_state.get('actions_count', 0)
    
    if actions_count >= MAX_ACTIONS_PER_MATCH:
        logger.warning(f"Пользователь {callback.from_user.id} превысил лимит действий в матче ({actions_count})")
        
        # Автоматически завершаем матч
        await callback.message.answer(
            "Достигнут лимит действий в матче. Матч будет автоматически завершен.",
            reply_markup=None
        )
        
        # Завершаем матч
        await finish_match(callback, state)
        return
    
    # Увеличиваем счетчик действий
    match_state['actions_count'] = actions_count + 1
    await state.update_data(match_state=match_state)
    
    # Проверяем, не обрабатывается ли уже момент
    if match_state.get('is_processing', False):
        try:
            await callback.answer("Дождитесь завершения текущего момента", show_alert=True)
        except Exception as e:
            logger.debug(f"Не удалось ответить на callback: {e}")
        return
    
    # Устанавливаем флаг обработки момента
    match_state['is_processing'] = True
    await state.update_data(match_state=match_state)
    
    try:
        # Получаем полный callback_data без префикса "defense_"
        action = callback.data[8:]  # Убираем "defense_" из начала
        
        # Безопасный ответ на callback
        try:
            await callback.answer()
        except Exception as e:
            logger.debug(f"Не удалось ответить на callback: {e}")
        
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
        logger.error(f"Ошибка при обработке защитного действия: {e}")
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)
        try:
            await callback.answer("Произошла ошибка. Попробуйте еще раз.", show_alert=True)
        except Exception as err:
            logger.debug(f"Не удалось ответить на callback после ошибки: {err}")
    finally:
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)

# Функция для обработки игрового момента
async def handle_goalkeeper_save(callback: types.CallbackQuery, match_state, state: FSMContext):
    action = callback.data.split('_')[1]
    try:
        # Проверяем наличие необходимых полей статистики
        if 'stats' not in match_state:
            match_state['stats'] = {
                "goals": 0,
                "assists": 0,
                "saves": 0,
                "tackles": 0,
                "fouls": 0,
                "passes": 0,
                "interceptions": 0,
                "clearances": 0,
                "throws": 0
            }
            
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
                match_state['stats']['saves'] = match_state['stats'].get('saves', 0) + 1
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
                    match_state['stats']['tackles'] = match_state['stats'].get('tackles', 0) + 1
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
                    match_state['stats']['throws'] = match_state['stats'].get('throws', 0) + 1
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
        [InlineKeyboardButton(text="⬅️ Отдать влево", callback_data="defense_pass_left")],
        [InlineKeyboardButton(text="⚽ Выбить", callback_data="defense_clear")],
        [InlineKeyboardButton(text="➡️ Отдать вправо", callback_data="defense_pass_right")]
    ])

async def handle_defender_tackle(callback: types.CallbackQuery, match_state, state: FSMContext):
    try:
        # Проверяем наличие необходимых полей статистики
        if 'stats' not in match_state:
            match_state['stats'] = {
                "goals": 0,
                "assists": 0,
                "saves": 0,
                "tackles": 0,
                "fouls": 0,
                "passes": 0,
                "interceptions": 0,
                "clearances": 0,
                "throws": 0
            }
            
        await send_photo_with_text(
            callback.message,
            'defense',
            'tackle_start.jpg',
            f"🛡️ {match_state['current_team']} в защите\n- Защитник готовится к отбору мяча"
        )
        await asyncio.sleep(3)
        
        if random.random() < 0.6:
            match_state['stats']['tackles'] = match_state['stats'].get('tackles', 0) + 1
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
        # Проверяем наличие необходимых полей статистики
        if 'stats' not in match_state:
            match_state['stats'] = {
                "goals": 0,
                "assists": 0,
                "saves": 0,
                "tackles": 0,
                "fouls": 0,
                "passes": 0,
                "interceptions": 0,
                "clearances": 0,
                "throws": 0
            }
            
        await send_photo_with_text(
            callback.message,
            'defense',
            'block_start.jpg',
            f"🚫 {match_state['current_team']} в защите\n- Защитник ставит блок"
        )
        await asyncio.sleep(3)
        
        if random.random() < 0.5:
            match_state['stats']['tackles'] = match_state['stats'].get('tackles', 0) + 1
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
        # Проверяем наличие необходимых полей статистики
        if 'stats' not in match_state:
            match_state['stats'] = {
                "goals": 0,
                "assists": 0,
                "saves": 0,
                "tackles": 0,
                "fouls": 0,
                "passes": 0,
                "interceptions": 0,
                "clearances": 0,
                "throws": 0
            }
            
        await send_photo_with_text(
            callback.message,
            'pass',
            'left.jpg',
            f"⬅️ {match_state['current_team']} с мячом\n- Защитник отдает пас влево"
        )
        await asyncio.sleep(3)
        
        if random.random() < 0.7:
            # Увеличиваем счетчик пасов, а не голевых передач
            match_state['stats']['passes'] = match_state['stats'].get('passes', 0) + 1
            await send_photo_with_text(
                callback.message,
                'pass',
                'success.jpg',
                "✅ Отличный пас!\n- Партнер получил мяч в выгодной позиции"
            )
            
            # Шанс на гол после успешной передачи
            if random.random() < 0.3:  # 30% шанс гола
                match_state['your_goals'] += 1
                # Засчитываем голевую передачу только если забит гол
                match_state['stats']['assists'] = match_state['stats'].get('assists', 0) + 1
                await send_photo_with_text(
                    callback.message,
                    'goals',
                    'goal.jpg',
                    f"⚽ ГООООЛ!\n- Партнер реализовал момент после вашей передачи! Счёт: {match_state['your_goals']}-{match_state['opponent_goals']}"
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
        # Проверяем наличие необходимых полей статистики
        if 'stats' not in match_state:
            match_state['stats'] = {
                "goals": 0,
                "assists": 0,
                "saves": 0,
                "tackles": 0,
                "fouls": 0,
                "passes": 0,
                "interceptions": 0,
                "clearances": 0,
                "throws": 0
            }
            
        await send_photo_with_text(
            callback.message,
            'pass',
            'right.jpg',
            f"➡️ {match_state['current_team']} с мячом\n- Защитник отдает пас вправо"
        )
        await asyncio.sleep(3)
        
        if random.random() < 0.7:
            # Увеличиваем счетчик пасов, а не голевых передач
            match_state['stats']['passes'] = match_state['stats'].get('passes', 0) + 1
            await send_photo_with_text(
                callback.message,
                'pass',
                'success.jpg',
                "✅ Отличный пас!\n- Партнер получил мяч в выгодной позиции"
            )
            
            # Шанс на гол после успешной передачи
            if random.random() < 0.3:  # 30% шанс гола
                match_state['your_goals'] += 1
                # Засчитываем голевую передачу только если забит гол
                match_state['stats']['assists'] = match_state['stats'].get('assists', 0) + 1
                await send_photo_with_text(
                    callback.message,
                    'goals',
                    'goal.jpg',
                    f"⚽ ГООООЛ!\n- Партнер реализовал момент после вашей передачи! Счёт: {match_state['your_goals']}-{match_state['opponent_goals']}"
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
        # Проверяем наличие необходимых полей статистики
        if 'stats' not in match_state:
            match_state['stats'] = {
                "goals": 0,
                "assists": 0,
                "saves": 0,
                "tackles": 0,
                "fouls": 0,
                "passes": 0,
                "interceptions": 0,
                "clearances": 0,
                "throws": 0
            }
            
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
                match_state['stats']['goals'] = match_state['stats'].get('goals', 0) + 1
                await send_photo_with_text(
                    callback.message,
                    'goals',
                    'goal.jpg',
                    f"⚽ ГООООЛ!\n- Невероятно! Защитник случайно забил гол! Счёт: {match_state['your_goals']}-{match_state['opponent_goals']}"
                )
            else:
                match_state['stats']['clearances'] = match_state['stats'].get('clearances', 0) + 1
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
        # Проверяем наличие необходимых полей статистики
        if 'stats' not in match_state:
            match_state['stats'] = {
                "goals": 0,
                "assists": 0,
                "saves": 0,
                "tackles": 0,
                "fouls": 0,
                "passes": 0,
                "interceptions": 0,
                "clearances": 0,
                "throws": 0
            }
            
        await send_photo_with_text(
            callback.message,
            'shot',
            'start.jpg',
            f"⚽ {match_state['current_team']} с мячом\n- Нападающий готовится к удару"
        )
        await asyncio.sleep(3)
        
        if random.random() < 0.25:  # Уменьшаем шанс гола с 0.4 до 0.25
            match_state['your_goals'] += 1
            match_state['stats']['goals'] = match_state['stats'].get('goals', 0) + 1
            await send_photo_with_text(
                callback.message,
                'goals',
                'goal.jpg',
                f"⚽ ГООООЛ!\n- Отличный удар! Счёт: {match_state['your_goals']}-{match_state['opponent_goals']}"
            )
            # После гола сразу продолжаем матч
            await continue_match(callback, match_state, state)
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
        # Проверяем наличие необходимых полей статистики
        if 'stats' not in match_state:
            match_state['stats'] = {
                "goals": 0,
                "assists": 0,
                "saves": 0,
                "tackles": 0,
                "fouls": 0,
                "passes": 0,
                "interceptions": 0,
                "clearances": 0,
                "throws": 0
            }
            
        await send_photo_with_text(
            callback.message,
            'pass',
            'start.jpg',
            f"🎯 {match_state['current_team']} с мячом\n- Нападающий ищет партнера для передачи"
        )
        await safe_sleep(2)
        
        if random.random() < 0.7:
            # Увеличиваем счетчик пасов, а не голевых передач
            match_state['stats']['passes'] = match_state['stats'].get('passes', 0) + 1
            await send_photo_with_text(
                callback.message,
                'pass',
                'success.jpg',
                "✅ Отличный пас!\n- Партнер получил мяч в выгодной позиции"
            )
            # Симулируем дальнейшую атаку команды
            await safe_sleep(2)
            # Шанс на гол после успешной передачи
            if random.random() < 0.45:  # 45% шанс гола
                # Увеличиваем счет команды и засчитываем голевую передачу
                match_state['your_goals'] += 1
                match_state['stats']['assists'] = match_state['stats'].get('assists', 0) + 1
                await send_photo_with_text(
                    callback.message,
                    'goals',
                    'goal.jpg',
                    f"⚽ ГООООЛ!\n- Партнер реализовал момент после вашей передачи! Счёт: {match_state['your_goals']}-{match_state['opponent_goals']}"
                )
            else:
                await send_photo_with_text(
                    callback.message,
                    'attack',
                    'shot_miss.jpg',
                    "❌ Удар неточный\n- Партнер не смог реализовать момент"
                )
            # Сохраняем состояние перед продолжением
            await state.update_data(match_state=match_state)
            # Продолжаем матч
            await continue_match(callback, match_state, state)
        else:
            await send_photo_with_text(
                callback.message,
                'pass',
                'intercept.jpg',
                "❌ Пас перехвачен\n- Соперник перехватил передачу"
            )
            await safe_sleep(1)
            await simulate_opponent_attack(callback, match_state)
            # Сохраняем состояние перед продолжением
            await state.update_data(match_state=match_state)
            await continue_match(callback, match_state, state)
    except Exception as e:
        logger.error(f"Ошибка в handle_forward_pass: {e}")
        # Продолжаем матч в случае ошибки
        try:
            await continue_match(callback, match_state, state)
        except Exception as continue_error:
            logger.error(f"Не удалось продолжить матч после ошибки: {continue_error}")
    finally:
        # Сбрасываем флаг обработки в любом случае
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)

async def handle_forward_dribble(callback: types.CallbackQuery, match_state, state: FSMContext):
    try:
        # Проверяем наличие необходимых полей статистики
        if 'stats' not in match_state:
            match_state['stats'] = {
                "goals": 0,
                "assists": 0,
                "saves": 0,
                "tackles": 0,
                "fouls": 0,
                "passes": 0,
                "interceptions": 0,
                "clearances": 0,
                "throws": 0
            }
            
        await send_photo_with_text(
            callback.message,
            'dribble',
            'start.jpg',
            f"🏃 {match_state['current_team']} с мячом\n- Нападающий начинает дриблинг"
        )
        await safe_sleep(2)
        
        if random.random() < 0.6:
            await send_photo_with_text(
                callback.message,
                'dribble',
                'success.jpg',
                "✅ Отличный дриблинг!\n- Нападающий обыграл защитника"
            )
            # Симулируем дальнейшую атаку после успешного дриблинга
            await safe_sleep(2)
            # Шанс на гол после успешного дриблинга
            if random.random() < 0.35:  # 35% шанс гола
                match_state['your_goals'] += 1
                match_state['stats']['goals'] = match_state['stats'].get('goals', 0) + 1
                await send_photo_with_text(
                    callback.message,
                    'goals',
                    'goal.jpg',
                    f"⚽ ГООООЛ!\n- Нападающий реализовал момент после дриблинга! Счёт: {match_state['your_goals']}-{match_state['opponent_goals']}"
                )
            else:
                await send_photo_with_text(
                    callback.message,
                    'shot',
                    'miss.jpg',
                    "❌ Удар неточный\n- Не удалось завершить атаку после дриблинга"
                )
            # Сохраняем состояние перед продолжением
            await state.update_data(match_state=match_state)
            # Продолжаем матч
            await continue_match(callback, match_state, state)
        else:
            await send_photo_with_text(
                callback.message,
                'dribble',
                'fail.jpg',
                "❌ Потеря мяча\n- Защитник отобрал мяч"
            )
            await safe_sleep(1)
            await simulate_opponent_attack(callback, match_state)
            # Сохраняем состояние перед продолжением
            await state.update_data(match_state=match_state)
            await continue_match(callback, match_state, state)
    except Exception as e:
        logger.error(f"Ошибка в handle_forward_dribble: {e}")
        # Продолжаем матч в случае ошибки
        try:
            await continue_match(callback, match_state, state)
        except Exception as continue_error:
            logger.error(f"Не удалось продолжить матч после ошибки: {continue_error}")
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
    # Проверяем наличие необходимых полей в match_state
    if 'stats' not in match_state:
        match_state['stats'] = {
            "goals": 0,
            "assists": 0,
            "saves": 0,
            "tackles": 0,
            "fouls": 0,
            "passes": 0,
            "interceptions": 0,
            "clearances": 0,
            "throws": 0
        }
        
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
    """
    Завершает матч и показывает результаты
    
    Устанавливает флаг match_finished и очищает состояние,
    чтобы предотвратить дальнейшие действия
    """
    data = await state.get_data()
    match_state = data.get('match_state')
    
    if not match_state:
        await callback.message.answer("Матч не найден.")
        return
    
    # Отмечаем матч как завершенный
    match_state['match_finished'] = True
    await state.update_data(match_state=match_state)
    
    player = await get_player(callback.from_user.id)
    if not player:
        logger.error(f"Не удалось получить данные игрока {callback.from_user.id} при завершении матча")
        await callback.message.answer("Произошла ошибка при сохранении статистики. Пожалуйста, обратитесь к администратору.")
        return
    
    matches = player.matches + 1
    wins = player.wins
    draws = player.draws
    losses = player.losses
    current_round = player.current_round + 1
    
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

    logger.info(
        f"Матч завершен: {player.club} {result} {match_state['opponent_team']} "
        f"({match_state['your_goals']}-{match_state['opponent_goals']})"
    )

    # Получаем новую дату после матча
    new_date = await advance_virtual_date(player)
    
    # Создаем статистику матча для обновления
    match_stats = {
        "matches": matches,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "current_round": current_round,
        "last_match_date": new_date
    }
    
    # Ограничиваем максимальную статистику за один матч
    MAX_STATS_PER_MATCH = 10  # Максимум голов, передач и т.д. за один матч
    
    # Добавляем статистику игрока для текущего матча
    if match_state['stats'].get('goals', 0) > 0:
        goals = min(match_state['stats']['goals'], MAX_STATS_PER_MATCH)
        match_stats["goals"] = player.goals + goals
    
    if match_state['stats'].get('assists', 0) > 0:
        assists = min(match_state['stats']['assists'], MAX_STATS_PER_MATCH)
        match_stats["assists"] = player.assists + assists
    
    if match_state['stats'].get('saves', 0) > 0:
        saves = min(match_state['stats']['saves'], MAX_STATS_PER_MATCH)
        match_stats["saves"] = player.saves + saves
    
    if match_state['stats'].get('tackles', 0) > 0:
        tackles = min(match_state['stats']['tackles'], MAX_STATS_PER_MATCH)
        match_stats["tackles"] = player.tackles + tackles
    
    # Обновляем статистику в базе данных
    update_success = await update_player_stats(
        user_id=callback.from_user.id,
        **match_stats
    )
    
    if not update_success:
        logger.error(f"Не удалось обновить статистику игрока {callback.from_user.id} при завершении матча")
        await callback.message.answer("Произошла ошибка при сохранении статистики. Статистика может отображаться некорректно.")
    
    # Форматируем дату для отображения с учетом возможных форматов даты
    try:
        # Проверяем формат даты и преобразуем соответственно
        if "-" in new_date:  # Формат YYYY-MM-DD
            formatted_date = datetime.strptime(new_date, "%Y-%m-%d").strftime("%d.%m.%Y")
        else:  # Формат DD.MM.YYYY
            formatted_date = new_date  # Дата уже в нужном формате
    except Exception as e:
        logger.error(f"Ошибка при форматировании даты '{new_date}': {e}")
        formatted_date = new_date  # В случае ошибки используем как есть
    
    stats = (f"{result_emoji} Матч завершен! Вы {result}!\n"
            f"🏆 Тур {match_state['current_round']} ФНЛ Серебро\n"
            f"📅 {formatted_date}\n\n"
            f"Итоговый счет: {match_state['your_goals']}-{match_state['opponent_goals']}\n\n"
            f"📊 Ваша статистика в матче:\n"
            f"Голы: {min(match_state['stats'].get('goals', 0), MAX_STATS_PER_MATCH)}\n"
            f"Голевые передачи: {min(match_state['stats'].get('assists', 0), MAX_STATS_PER_MATCH)}\n"
            f"Сейвы: {min(match_state['stats'].get('saves', 0), MAX_STATS_PER_MATCH)}\n"
            f"Отборы: {min(match_state['stats'].get('tackles', 0), MAX_STATS_PER_MATCH)}\n\n"
            f"📊 Общая статистика:\n"
            f"Матчи: {matches}\n"
            f"Победы: {wins}\n"
            f"Ничьи: {draws}\n"
            f"Поражения: {losses}")
    
    # Проверяем возможность перехода
    player = await get_player(callback.from_user.id)
    league, offers = get_transfer_offers(player)
    if offers:
        logger.info(f"Игроку {player.name} (ID: {callback.from_user.id}) поступили предложения о переходе")
        await callback.message.answer(
            "Вам поступили предложения от других клубов! Хотите перейти?",
            reply_markup=get_transfer_keyboard(offers, league)
        )
        try:
            await callback.answer()
        except Exception as e:
            logger.debug(f"Не удалось ответить на callback: {e}")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="show_stats")],
        [InlineKeyboardButton(text="🏠 Вернуться в меню", callback_data="return_to_menu")]
    ])
    
    # Очищаем состояние перед завершением
    await state.clear()
    await state.set_state(GameStates.playing)
    
    await callback.message.answer(stats, reply_markup=keyboard)
    try:
        await callback.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback: {e}")

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
            "Статистика не найдена. Начните игру с команды /start"
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
    
    await callback.message.answer(stats)
    try:
        await callback.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback: {e}")

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
    try:
        await callback.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback: {e}")

async def simulate_opponent_attack(callback: types.CallbackQuery, match_state):
    attack_type = random.choices(
        ['dribble', 'shot', 'pass'],
        weights=[0.3, 0.4, 0.3]
    )[0]
    
    # Проверяем наличие необходимых полей в match_state
    if 'stats' not in match_state:
        match_state['stats'] = {
            "goals": 0,
            "assists": 0,
            "saves": 0,
            "tackles": 0,
            "fouls": 0,
            "passes": 0,
            "interceptions": 0,
            "clearances": 0,
            "throws": 0
        }
    
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
                    match_state['stats']['saves'] = match_state['stats'].get('saves', 0) + 1
                    await send_photo_with_text(
                        callback.message,
                        'defense',
                        'save.jpg',
                        "✅ Наш вратарь отразил удар\n- Вратарь совершил отличный сейв"
                    )
            else:
                match_state['stats']['tackles'] = match_state['stats'].get('tackles', 0) + 1
                await send_photo_with_text(
                    callback.message,
                    'defense',
                    'tackle.jpg',
                    "✅ Наш защитник успел подстраховать\n- Защитник не дал сопернику ударить"
                )
        else:
            match_state['stats']['tackles'] = match_state['stats'].get('tackles', 0) + 1
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
            match_state['stats']['saves'] = match_state['stats'].get('saves', 0) + 1
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
                match_state['stats']['saves'] = match_state['stats'].get('saves', 0) + 1
                await send_photo_with_text(
                    callback.message,
                    'defense',
                    'save.jpg',
                    "✅ Наш вратарь отразил удар\n- Вратарь совершил отличный сейв"
                )
        else:
            match_state['stats']['tackles'] = match_state['stats'].get('tackles', 0) + 1
            await send_photo_with_text(
                callback.message,
                'defense',
                'intercept.jpg',
                "✅ Наш защитник перехватил пас\n- Защитник успешно перехватил передачу"
            )

async def reset_player_stats(user_id):
    try:
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
            logger.info(f"Статистика игрока {user_id} сброшена")
    except Exception as e:
        logger.error(f"Ошибка при сбросе статистики игрока {user_id}: {e}")
        raise

async def delete_player(user_id):
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(Player).where(Player.user_id == user_id)
            )
            await session.commit()
            logger.info(f"Игрок {user_id} удален из базы данных")
    except Exception as e:
        logger.error(f"Ошибка при удалении игрока {user_id}: {e}")
        raise

@dp.message(Command("reset_stats"))
async def cmd_reset_stats(message: types.Message, state: FSMContext):
    logger.info(f"Пользователь {message.from_user.id} запросил сброс статистики")
    
    # Проверяем, не идет ли сейчас матч
    data = await state.get_data()
    if data.get('match_state'):
        logger.warning(f"Пользователь {message.from_user.id} попытался сбросить статистику во время матча")
        await message.answer(
            "❌ Сейчас идет матч! Дождитесь его завершения.",
            reply_markup=get_main_keyboard()
        )
        return
    
    player = await get_player(message.from_user.id)
    if not player:
        logger.warning(f"Пользователь {message.from_user.id} попытался сбросить статистику без создания игрока")
        await message.answer(
            "❌ Вы еще не создали своего игрока. Используйте команду /start",
            reply_markup=get_main_keyboard()
        )
        return
    
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
    logger.info(f"Пользователь {callback.from_user.id} подтвердил сброс статистики")
    await reset_player_stats(callback.from_user.id)
    await callback.message.edit_text(
        "✅ Статистика успешно сброшена!\n"
        "Используйте команду /start для начала новой карьеры."
    )
    try:
        await callback.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback: {e}")

@dp.callback_query(lambda c: c.data == "cancel_reset")
async def cancel_reset_callback(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "❌ Сброс статистики отменен.\n"
        "Ваша статистика сохранена."
    )
    try:
        await callback.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback: {e}")

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
    logger.info(f"Пользователь {message.from_user.id} запросил удаление игрока")
    
    # Проверяем, не идет ли сейчас матч
    data = await state.get_data()
    if data.get('match_state'):
        logger.warning(f"Пользователь {message.from_user.id} попытался удалить игрока во время матча")
        await message.answer(
            "❌ Сейчас идет матч! Дождитесь его завершения."
        )
        return
    
    # Проверяем, существует ли игрок
    player = await get_player(message.from_user.id)
    if not player:
        logger.warning(f"Пользователь {message.from_user.id} попытался удалить несуществующего игрока")
        await message.answer(
            "❌ Игрок не найден в базе данных."
        )
        return
    
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
    logger.info(f"Пользователь {callback.from_user.id} подтвердил удаление игрока")
    await delete_player(callback.from_user.id)
    await callback.message.edit_text(
        "✅ Игрок успешно удален!\n"
        "Используйте команду /start для создания нового игрока."
    )
    try:
        await callback.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback: {e}")

@dp.callback_query(lambda c: c.data == "cancel_delete")
async def cancel_delete_callback(callback: types.CallbackQuery, state: FSMContext):
    logger.info(f"Пользователь {callback.from_user.id} отменил удаление игрока")
    await callback.message.edit_text(
        "❌ Удаление игрока отменено.\n"
        "Ваши данные сохранены."
    )
    try:
        await callback.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback: {e}")

@dp.message(Command("admin_delete_player"))
async def cmd_admin_delete_player(message: types.Message, state: FSMContext):
    logger.info(f"Пользователь {message.from_user.id} запросил административное удаление игрока")
    
    # Проверяем, является ли пользователь администратором
    if message.from_user.id != 5259325234:  # Только для вас
        logger.warning(f"Пользователь {message.from_user.id} попытался использовать админ-команду")
        await message.answer("❌ У вас нет прав для выполнения этой команды.")
        return
    
    # Получаем ID игрока из аргументов команды
    try:
        user_id = int(message.text.split()[1])
    except (IndexError, ValueError):
        logger.warning(f"Некорректный формат команды от администратора {message.from_user.id}")
        await message.answer("❌ Укажите ID игрока: /admin_delete_player <ID>")
        return
    
    # Проверяем, существует ли игрок
    player = await get_player(user_id)
    if not player:
        logger.warning(f"Администратор {message.from_user.id} попытался удалить несуществующего игрока {user_id}")
        await message.answer(f"❌ Игрок с ID {user_id} не найден в базе данных.")
        return
    
    # Удаляем игрока
    logger.info(f"Администратор {message.from_user.id} удалил игрока {player.name} (ID: {user_id})")
    await delete_player(user_id)
    await message.answer(f"✅ Игрок {player.name} (ID: {user_id}) успешно удален из базы данных.")

@dp.message(Command("play"))
async def cmd_play(message: types.Message, state: FSMContext):
    """Обработчик команды /play - перенаправляет на запуск матча через кнопку"""
    logger.info(f"Пользователь {message.from_user.id} использовал команду /play")
    
    player = await get_player(message.from_user.id)
    if not player:
        logger.warning(f"Пользователь {message.from_user.id} попытался начать матч без создания игрока")
        await message.answer(
            "Сначала создайте своего игрока с помощью команды /start"
        )
        return
    
    await message.answer(
        "Для запуска матча используйте кнопку 'Играть матч' в главном меню:",
        reply_markup=get_main_keyboard()
    )

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message, state: FSMContext):
    """Обработчик команды /stats - перенаправляет на просмотр статистики через кнопку"""
    logger.info(f"Пользователь {message.from_user.id} использовал команду /stats")
    
    player = await get_player(message.from_user.id)
    if not player:
        logger.warning(f"Пользователь {message.from_user.id} попытался просмотреть статистику без создания игрока")
        await message.answer(
            "Сначала создайте своего игрока с помощью команды /start"
        )
        return
    
    # Передаем управление обработчику кнопки "Статистика"
    callback_query = types.CallbackQuery(
        id="stats_command",
        from_user=message.from_user,
        chat_instance="stats_command_instance",
        message=message,
        data="show_stats"
    )
    
    await show_stats_callback(callback_query, state)

async def main():
    try:
        logger.info("Запуск бота...")
        await init_db()
        logger.info("База данных инициализирована, начинаем поллинг...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске бота: {e}")
        raise

# Функция для создания персонального календаря игрока
def create_player_calendar(club_name):
    """
    Создает личный календарь матчей для игрока заданного клуба
    Возвращает JSON строку с календарем на весь сезон (18 туров)
    """
    try:
        player_calendar = []
        
        # Для каждого тура находим матч с участием клуба игрока
        for home_team, away_team, round_num in MATCH_CALENDAR:
            # Проверяем, участвует ли клуб игрока в матче
            if home_team == club_name or away_team == club_name:
                # Определяем соперника и флаг домашнего матча
                opponent = away_team if home_team == club_name else home_team
                is_home = (home_team == club_name)
                
                # Добавляем матч в персональный календарь
                player_calendar.append({
                    "round": round_num,
                    "opponent": opponent,
                    "is_home": is_home
                })
        
        # Сортируем по туру
        player_calendar.sort(key=lambda match: match["round"])
        
        # Проверяем, что календарь не пустой
        if not player_calendar:
            logger.error(f"Не удалось создать календарь для клуба {club_name}")
            return json.dumps([])
        
        logger.info(f"Создан персональный календарь для клуба {club_name} из {len(player_calendar)} матчей")
        return json.dumps(player_calendar)
    except Exception as e:
        logger.error(f"Ошибка при создании календаря для клуба {club_name}: {e}")
        return json.dumps([])

async def generate_calendar_visualization(player, upcoming_matches):
    """Создает визуальное представление календаря для игрока с эмодзи"""
    try:
        # Проверяем наличие матчей
        if not upcoming_matches:
            return "Календарь пуст"
        
        # Создаем текст календаря
        calendar_text = f"📅 Календарь матчей {player.club}\n\n"
        
        for match in upcoming_matches:
            round_num = match["round"]
            opponent = match["opponent"]
            is_home = match["is_home"]
            
            # Эмодзи для матча
            location_emoji = "🏠" if is_home else "🚌"
            
            # Сила соперника (в зависимости от лиги)
            opponent_strength = FNL_SILVER_CLUBS.get(opponent, {}).get("strength", 50)
            if opponent_strength >= 70:
                difficulty_emoji = "⭐⭐⭐" # Сильный соперник
            elif opponent_strength >= 50:
                difficulty_emoji = "⭐⭐" # Средний соперник
            else:
                difficulty_emoji = "⭐" # Слабый соперник
            
            # Отмечаем текущий тур
            current_marker = "➡️ " if round_num == player.current_round else "   "
            
            # Добавляем строку с матчем
            calendar_text += f"{current_marker}Тур {round_num}: {location_emoji} {opponent} {difficulty_emoji}\n"
        
        calendar_text += "\n📋 Пояснения:\n"
        calendar_text += "➡️ - Ваш следующий матч\n"
        calendar_text += "🏠 - Домашний матч\n"
        calendar_text += "🚌 - Выездной матч\n"
        calendar_text += "⭐⭐⭐ - Сильный соперник\n"
        calendar_text += "⭐⭐ - Средний соперник\n"
        calendar_text += "⭐ - Слабый соперник\n"
        
        return calendar_text
    except Exception as e:
        logger.error(f"Ошибка при создании визуализации календаря: {e}")
        return "Ошибка при создании календаря"

@dp.callback_query(lambda c: c.data == "show_calendar")
async def show_calendar_callback(callback: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Календарь', показывает ближайшие матчи игрока"""
    # Очищаем состояние матча
    await state.set_data({})
    await state.set_state(GameStates.playing)
    
    if not await check_subscription(callback.from_user.id):
        await callback.message.answer(
            "Для просмотра календаря необходимо подписаться на наш канал!",
            reply_markup=get_subscription_keyboard()
        )
        return
    
    player = await get_player(callback.from_user.id)
    if not player:
        await callback.message.answer(
            "Календарь не найден. Начните игру с команды /start"
        )
        return
    
    # Получаем ближайшие 10 матчей (или меньше, если в календаре меньше)
    upcoming_matches = await get_player_next_matches(player, 10)
    
    if not upcoming_matches:
        await callback.message.answer(
            "📅 Календарь матчей\n\n"
            "❌ Не удалось загрузить календарь или у вас нет запланированных матчей.\n"
            "Попробуйте позже или обратитесь к администратору."
        )
        return
    
    # Генерируем визуальное представление календаря
    calendar_text = await generate_calendar_visualization(player, upcoming_matches)
    
    # Добавляем кнопку для возврата в меню
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Вернуться в меню", callback_data="return_to_menu")]
    ])
    
    await callback.message.answer(calendar_text, reply_markup=keyboard)
    try:
        await callback.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback: {e}")

@dp.message(Command("calendar"))
async def cmd_calendar(message: types.Message, state: FSMContext):
    """Обработчик команды /calendar для просмотра календаря матчей"""
    logger.info(f"Пользователь {message.from_user.id} запросил календарь матчей")
    
    # Проверяем, не идет ли сейчас матч
    data = await state.get_data()
    if data.get('match_state'):
        logger.warning(f"Пользователь {message.from_user.id} попытался просмотреть календарь во время матча")
        await message.answer(
            "❌ Сейчас идет матч! Дождитесь его завершения."
        )
        return
    
    if not await check_subscription(message.from_user.id):
        logger.warning(f"Пользователь {message.from_user.id} не подписан на канал при попытке просмотреть календарь")
        await message.answer(
            "Для просмотра календаря необходимо подписаться на наш канал!",
            reply_markup=get_subscription_keyboard()
        )
        return
    
    player = await get_player(message.from_user.id)
    if not player:
        logger.warning(f"Пользователь {message.from_user.id} попытался просмотреть календарь без создания игрока")
        await message.answer(
            "Сначала создайте своего игрока с помощью команды /start"
        )
        return
    
    # Создаем фейковый callback query для вызова обработчика календаря
    callback_query = types.CallbackQuery(
        id="calendar_command",
        from_user=message.from_user,
        chat_instance="calendar_command_instance",
        message=message,
        data="show_calendar"
    )
    
    await show_calendar_callback(callback_query, state)

async def get_player_next_matches(player, count=5):
    """Получает ближайшие матчи из персонального календаря игрока"""
    try:
        # Проверяем наличие атрибута personal_calendar
        if not hasattr(player, 'personal_calendar') or not player.personal_calendar:
            logger.warning(f"У игрока {player.name} (ID: {player.user_id}) отсутствует календарь, создаем новый")
            # Создаем календарь для игрока, если его нет
            calendar_json = create_player_calendar(player.club)
            # Сохраняем календарь в базу
            await update_player_stats(
                user_id=player.user_id,
                personal_calendar=calendar_json
            )
            calendar = json.loads(calendar_json)
        else:
            # Парсим JSON календарь
            calendar = json.loads(player.personal_calendar)
        
        # Находим текущий тур
        current_round = player.current_round if player.matches > 0 else 1
        
        # Фильтруем матчи, которые еще не сыграны (тур >= текущий)
        upcoming_matches = [match for match in calendar if match["round"] >= current_round]
        
        # Сортируем по номеру тура
        upcoming_matches.sort(key=lambda x: x["round"])
        
        # Возвращаем указанное количество ближайших матчей
        return upcoming_matches[:count]
    except Exception as e:
        logger.error(f"Ошибка при получении календаря игрока {player.name}: {e}")
        return []

# Функция создания календаря для нового сезона
async def start_new_season(player):
    """
    Создаёт новый календарь для игрока на новый сезон
    и обновляет данные игрока в базе данных
    """
    try:
        logger.info(f"Создание нового сезона для игрока {player.name} (ID: {player.user_id})")
        
        # Создаем новый календарь для клуба игрока
        new_calendar = create_player_calendar(player.club)
        
        # Обновляем данные игрока в базе
        await update_player_stats(
            user_id=player.user_id,
            personal_calendar=new_calendar,
            current_round=1  # Сбрасываем текущий тур на 1
        )
        
        logger.info(f"Новый сезон начат для игрока {player.name} (ID: {player.user_id})")
        return True
    except Exception as e:
        logger.error(f"Ошибка при создании нового сезона для игрока {player.name} (ID: {player.user_id}): {e}")
        return False

# Функция для полного сброса базы данных
async def reset_database():
    """Полностью сбрасывает базу данных, удаляя и пересоздавая все таблицы"""
    try:
        logger.warning("Начинаем полный сброс базы данных...")
        
        # Удаляем все таблицы
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            logger.info("Все таблицы успешно удалены")
            
            # Пересоздаем таблицы
            await conn.run_sync(Base.metadata.create_all)
            logger.info("Таблицы успешно пересозданы")
        
        logger.warning("База данных полностью сброшена")
        return True
    except Exception as e:
        logger.error(f"Критическая ошибка при сбросе базы данных: {e}")
        return False

@dp.message(Command("reset_database"))
async def cmd_reset_database(message: types.Message, state: FSMContext):
    """Команда для полного сброса базы данных"""
    logger.warning(f"Пользователь {message.from_user.id} запросил сброс всей базы данных")
    
    # Проверяем, является ли пользователь администратором
    if message.from_user.id != 5259325234:  # ID администратора
        logger.warning(f"Пользователь {message.from_user.id} попытался сбросить базу данных без прав администратора")
        await message.answer("❌ Куда ты лезешь, умник")
        return
    
    # Запрашиваем подтверждение
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, полностью сбросить", callback_data="confirm_reset_database")],
        [InlineKeyboardButton(text="❌ Нет, отмена", callback_data="cancel_reset_database")]
    ])
    
    await message.answer(
        "⚠️ ВНИМАНИЕ! ⚠️\n\n"
        "Вы собираетесь полностью сбросить базу данных!\n"
        "Все данные игроков, включая статистику и прогресс, будут безвозвратно удалены.\n\n"
        "Вы абсолютно уверены, что хотите продолжить?",
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data == "confirm_reset_database")
async def confirm_reset_database_callback(callback: types.CallbackQuery, state: FSMContext):
    """Подтверждение сброса базы данных"""
    # Проверяем, является ли пользователь администратором
    if callback.from_user.id != 5259325234:  # ID администратора
        logger.warning(f"Пользователь {callback.from_user.id} попытался сбросить базу данных без прав администратора")
        await callback.message.answer("❌ У вас нет прав для выполнения этой операции.")
        await callback.answer()
        return
    
    await callback.message.edit_text("🔄 Выполняется сброс базы данных...")
    
    # Выполняем сброс
    success = await reset_database()
    
    if success:
        await callback.message.edit_text(
            "✅ База данных успешно сброшена!\n"
            "Все данные удалены и структура таблиц пересоздана."
        )
        logger.warning(f"Администратор {callback.from_user.id} успешно выполнил полный сброс базы данных")
    else:
        await callback.message.edit_text(
            "❌ Произошла ошибка при сбросе базы данных.\n"
            "Проверьте логи для получения деталей."
        )
        logger.error(f"Ошибка при попытке сброса базы данных администратором {callback.from_user.id}")
    
    try:
        await callback.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback: {e}")

@dp.callback_query(lambda c: c.data == "cancel_reset_database")
async def cancel_reset_database_callback(callback: types.CallbackQuery, state: FSMContext):
    """Отмена сброса базы данных"""
    await callback.message.edit_text(
        "✅ Сброс базы данных отменен.\n"
        "Данные не были изменены."
    )
    logger.info(f"Пользователь {callback.from_user.id} отменил сброс базы данных")
    
    try:
        await callback.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback: {e}")

async def start_match(message, match_state, state: FSMContext):
    """Запускает игровой процесс, отображает первое игровое сообщение"""
    try:
        # Получаем информацию о матче
        current_team = match_state['current_team']
        opponent_team = match_state['opponent_team']
        current_round = match_state['current_round']
        position = match_state['position']
        is_home = match_state.get('is_home', True)
        
        # Получаем виртуальную дату
        virtual_date = match_state.get('virtual_date', datetime.now().strftime("%d.%m.%Y"))
        
        # Начинаем с нулевой минуты
        match_state['minute'] = 0
        
        # Инициализируем начальные параметры
        match_state['your_goals'] = 0
        match_state['opponent_goals'] = 0
        match_state['is_processing'] = False
        match_state['actions_count'] = 0  # Счетчик действий игрока
        
        # Инициализируем статистику всеми полями, чтобы избежать KeyError
        match_state['stats'] = {
            "goals": 0,
            "assists": 0,
            "saves": 0,
            "tackles": 0,
            "fouls": 0,
            "passes": 0,
            "interceptions": 0,
            "clearances": 0,
            "throws": 0
        }
        
        # Добавляем флаг атаки соперника для защитников и вратарей
        if position in ["Вратарь", "Защитник"]:
            match_state['is_opponent_attack'] = True
        else:
            match_state['is_opponent_attack'] = False
        
        # Сохраняем обновленное состояние
        await state.update_data(match_state=match_state)
        
        # Формируем текст сообщения
        match_text = (
            f"🏆 <b>Тур {current_round} ФНЛ Серебро</b>\n"
            f"📅 {virtual_date}\n\n"
        )
        
        if is_home:
            match_text += f"🏠 <b>{current_team}</b> vs <b>{opponent_team}</b>\n"
        else:
            match_text += f"🚌 <b>{current_team}</b> vs <b>{opponent_team}</b>\n"
        
        match_text += f"⏱️ 0' минута. Счёт: 0-0\n\n"
        
        # Разные сообщения в зависимости от позиции
        if position in ["Вратарь", "Защитник"]:
            match_text += f"⚠️ {opponent_team} начинает атаку!\nВыберите действие:"
        else:
            match_text += f"⚽ {current_team} владеет мячом.\nВыберите действие:"
        
        # Отправляем сообщение с кнопками
        new_message = await message.answer(
            match_text,
            parse_mode="HTML",
            reply_markup=get_match_actions_keyboard(position)
        )
        
        # Обновляем ID последнего сообщения
        match_state['last_message_id'] = new_message.message_id
        await state.update_data(match_state=match_state)
        
        logger.info(f"Матч успешно начат: {current_team} vs {opponent_team} (Тур {current_round})")
        
    except Exception as e:
        logger.error(f"Ошибка при запуске матча: {e}")
        await message.answer(
            "Произошла ошибка при запуске матча. Пожалуйста, попробуйте снова.",
            reply_markup=get_main_keyboard()
        )

@dp.callback_query(lambda c: c.data.startswith('continue_match_'))
async def handle_continue_match(callback: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки продолжения матча"""
    data = await state.get_data()
    match_state = data.get('match_state')
    
    if not match_state:
        await callback.message.answer(
            "Матч не начат или уже завершен. Нажмите 'Играть матч' для начала нового матча."
        )
        try:
            await callback.answer("Матч не активен", show_alert=True)
        except Exception as e:
            logger.debug(f"Не удалось ответить на callback: {e}")
        return
    
    # Проверяем, завершен ли матч
    if match_state.get('match_finished', False):
        await callback.message.answer(
            "Матч уже завершен. Нажмите 'Играть матч' для начала нового матча."
        )
        try:
            await callback.answer("Матч уже завершен", show_alert=True)
        except Exception as e:
            logger.debug(f"Не удалось ответить на callback: {e}")
        return
    
    # Проверяем, не обрабатывается ли уже момент
    if match_state.get('is_processing', False):
        try:
            await callback.answer("Дождитесь завершения текущего момента", show_alert=True)
        except Exception as e:
            logger.debug(f"Не удалось ответить на callback: {e}")
        return
    
    # Устанавливаем флаг обработки момента
    match_state['is_processing'] = True
    await state.update_data(match_state=match_state)
    
    try:
        # Безопасный ответ на callback
        try:
            await callback.answer()
        except Exception as e:
            logger.debug(f"Не удалось ответить на callback: {e}")
        
        # Продолжаем матч
        await continue_match(callback, match_state, state)
    except Exception as e:
        logger.error(f"Ошибка при продолжении матча: {e}")
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)
        try:
            await callback.answer("Произошла ошибка. Попробуйте еще раз.", show_alert=True)
        except Exception as err:
            logger.debug(f"Не удалось ответить на callback после ошибки: {err}")
    finally:
        match_state['is_processing'] = False
        await state.update_data(match_state=match_state)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}")