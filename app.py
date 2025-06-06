import models, os, uvicorn, json, mysql.connector
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from database import SessionLocal
from sqlalchemy.orm import Session
from datetime import date, datetime
from sqlalchemy import func
from datetime import datetime, timedelta
from dotenv import load_dotenv

from service.convert_diary_format import writer_workflow
from service.summary_diary import summary_workflow
from service.emotion import request_gpt
from service.diary_psy import request_psy
from service.dalle_diary import generate_mone_pastel_image

load_dotenv()

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# CORS 허용 정책 추가
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],    # 모든 HTTP 메소드 허용
    allow_headers=["*"],    # 모든 헤더 허용
)

# 회원가입 API
class SignupData(BaseModel):
    username: str
    email: str
    password: str

@app.post("/api/signup")
async def signup(data: SignupData, db: Session = Depends(get_db)):
    # 이메일 중복 체크
    existing_user = db.query(models.User).filter(models.User.email == data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="이메일이 이미 사용 중입니다.")
    # 새 회원 등록
    new_user = models.User(
        username=data.username,
        email=data.email,
        password=data.password
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "회원가입 성공!", "user": {"email": new_user.email, "name": new_user.username}}

# 로그인 API
class UserLogin(BaseModel):
    email: str
    password: str
    
@app.post("/api/login")
async def login(user: UserLogin, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if not db_user:
        return {"success": False, "message": "이메일 또는 비밀번호가 잘못되었습니다.1"}
    if user.password != db_user.password:
        return {"success": False, "message": "이메일 또는 비밀번호가 잘못되었습니다.2"}
    return {"success": True, "message": "로그인 성공!", "user_id": db_user.user_id}

# 기록 생성
class TempDiaryData(BaseModel):
    user_id: int
    title: str
    content: str
    created_at: datetime

@app.post("/api/temp_diary/create")
async def create_temp_diary(data: TempDiaryData, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.user_id == data.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    
    # 새로운 temp_diary 생성
    new_temp_diary = models.TempDiary(
        user_id=user.user_id,
        title=data.title,
        content=data.content,
        created_at=data.created_at
    )

    db.add(new_temp_diary)
    db.commit()
    db.refresh(new_temp_diary)
    
    return {"message": "기록 성공", "temp_diary_id": new_temp_diary.temp_diary_id}


# 기록 조회
@app.get("/api/temp_diary/read")
async def read_temp_diary(user_id: int, db: Session = Depends(get_db)):
    
    today = date.today()  # 오늘 날짜 (예: 2025-05-25)
    # 오늘 date와 동일한 날짜의 기록들 쿼리
    diaries_today = db.query(models.TempDiary).filter(
        models.TempDiary.user_id == user_id,
        func.date(models.TempDiary.created_at) == today
    ).all()

    return diaries_today

# 기록 수정
class UpdateTempDiary(BaseModel):
    title: str = None
    content: str = None

@app.put("/api/temp_diary/{temp_diary_id}")
async def update_temp_diary(temp_diary_id: int, data: UpdateTempDiary, db: Session = Depends(get_db)):
    # 수정할 기록 찾기
    temp_diary = db.query(models.TempDiary).filter(models.TempDiary.temp_diary_id == temp_diary_id).first()

    if not temp_diary:
        raise HTTPException(status_code=404, detail="일기를 찾을 수 없습니다.")

    if data.title is not None:
        temp_diary.title = data.title
    if data.content is not None:
        temp_diary.content = data.content
        
    db.commit()

    return {"message": "수정 성공"}

# 기록 삭제
@app.delete("/api/temp_diary/delete")
async def delete_temp_diary(temp_diary_id: int, db: Session = Depends(get_db)):
    # 삭제할 기록 찾기
    temp_diary = db.query(models.TempDiary).filter(models.TempDiary.temp_diary_id == temp_diary_id).first()
    
    if not temp_diary:
        raise HTTPException(status_code=404, detail="일기를 찾을 수 없습니다.")

    # 삭제 수행
    db.delete(temp_diary)
    db.commit()

    return {"message": "기록 삭제 성공"}

# 예시
@app.get("/api/diary/hyunji")
async def read_diary():
    return "hi"

# 일기 작성
class DiaryData(BaseModel):
    user_id: int
    content: str
    created_at: datetime

@app.post("/api/diary/create")
async def create_diary(data: DiaryData, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.user_id == data.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    
    diary_summary = await summary_workflow(data.content)

    # 새로운 diary 생성
    new_diary = models.Diary(
        user_id=user.user_id,
        content=data.content,
        created_at=data.created_at,
        summary=diary_summary
    )

    db.add(new_diary)
    db.commit()
    db.refresh(new_diary)

    analysis_result = request_gpt(data.content)
    
    response_text = analysis_result.get('response', '')

    json_str = response_text.strip()
    if json_str.startswith("```json"):
        json_str = json_str[len("```json"):].strip()
    if json_str.endswith("```"):
        json_str = json_str[:-3].strip()
        
    if isinstance(analysis_result, dict):
        try:
            response_text = analysis_result['response']  # 문자열 추출

            # "```json"과 "```" 문자를 제거하여 JSON 문자열만 추출
            json_str = response_text.strip()
            if json_str.startswith("```json"):
                json_str = json_str[len("```json"):].strip()
            if json_str.endswith("```"):
                json_str = json_str[:-3].strip()

            # JSON 문자열을 딕셔너리로 로드
            json_data = json.loads(json_str)
            first_item = json_data[0]  # 리스트의 첫 번째 요소
            emotions = first_item['감정']

            joy = emotions.get("기쁨", 0)
            sadness = emotions.get("슬픔", 0)
            anger = emotions.get("분노", 0)
            fear = emotions.get("공포", 0)
            disgust = emotions.get("혐오", 0)
            anxiety = emotions.get("불안", 0)
            envy = emotions.get("부러움", 0)
            bewilderment = emotions.get("당황", 0)
            boredom = emotions.get("따분", 0)

        except Exception as e:
            print("파싱 에러:", e)
            joy = sadness = anger = fear = disgust = anxiety = envy = bewilderment = boredom = 0

        # 감정 저장
        new_emotion = models.Emotion(
            user_id=data.user_id,
            diary_id=new_diary.diary_id,
            joy=joy,
            sadness=sadness,
            anger=anger,
            fear=fear,
            disgust=disgust,
            anxiety=anxiety,
            envy=envy,
            bewilderment=bewilderment,
            boredom=boredom
        )

        db.add(new_emotion)
        db.commit()
        db.refresh(new_emotion)

    else:
        raise HTTPException(status_code=400, detail="감정 분석 실패 또는 유효하지 않은 결과입니다.")

    ## 여기다가 작성하겠죠 그 감정분석을
    
    return {"message": "일기 기록 및 감정 분석 성공", "diary_id": new_diary.diary_id}

# 일기 조회 (데이트 정보를 리엑트에서 받아옴)
    # 데이트 인포를 리엑트에서 받아오면 해당하는 날짜의 일기를 조회
    # 리엑트에서 받아온 날짜 (예: 2025-05-25)
@app.get("/api/diary/read")
async def read_diary_by_date(user_id: int, selected_date: str, db: Session = Depends(get_db)):
    target_date = datetime.strptime(selected_date, "%Y-%m-%d").date()

    diary_entries = db.query(models.Diary).filter(
        models.Diary.user_id == user_id,
        func.date(models.Diary.created_at) == target_date
    ).all()

    if not diary_entries:
        return {"message": "일기가 없습니다."}

    return diary_entries

# 일기 삭제
@app.delete("/api/diary/delete")
async def delete_temp_diary(diary_id: int, db: Session = Depends(get_db)):
    # 삭제할 기록 찾기
    diary = db.query(models.Diary).filter(models.Diary.diary_id == diary_id).first()
    
    if not diary:
        raise HTTPException(status_code=404, detail="일기를 찾을 수 없습니다.")

    # 삭제 수행
    db.delete(diary)
    db.commit()

    return {"message": "일기 삭제 성공"}

# 일기 수정
class UpdateDiary(BaseModel):
    title: str = None
    content: str = None

@app.put("/api/diary/{diary_id}")
async def update_diary(diary_id: int, data: UpdateDiary, db: Session = Depends(get_db)):
    # 수정할 기록 찾기
    diary = db.query(models.Diary).filter(models.Diary.diary_id == diary_id).first()

    if not diary:
        raise HTTPException(status_code=404, detail="일기를 찾을 수 없습니다.")

    if data.title is not None:
        diary.title = data.title
    if data.content is not None:
        diary.content = data.content
        
    db.commit()

    return {"message": "수정 성공"}

# 일기형태 변환
class QuestionRequest(BaseModel):
    question_text: str
    user_id: int

@app.post("/generate_diary")
async def generate_diary(request: QuestionRequest, db: Session = Depends(get_db)):
    user_id = request.user_id
    user = db.query(models.User).filter(models.User.user_id == user_id).first()
    reference = user.reference_text if user and user.reference_text else None
    result = await writer_workflow(request.question_text, reference)
    return {"일기 변환": result}

# 유저정보 수정
class UpdateUserInfo(BaseModel):
    email: str = None
    password: str = None

@app.put("/api/users/{user_id}")
async def update_user(user_id: int, data: UpdateUserInfo, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.user_id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="유저를 찾을 수 없습니다.")
    
    if data.email is not None:
        user.email = data.email
    if data.password is not None:
        user.password = data.password
        
    db.commit()

    return {"message": "유저 정보 수정 성공"}

# 유저정보 조회
@app.get("/api/users/{user_id}")
async def get_user_email(user_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="유저를 찾을 수 없음")
    user_data = {
        "user_id": user.user_id,
        "reference_text": user.reference_text
    }
    return user_data

# reference 선택
class ReferenceData(BaseModel):
    reference_text: str = None

@app.put("/api/users/reference/{user_id}")
async def select_reference(user_id: int, data: ReferenceData, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.user_id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="유저를 찾을 수 없습니다.")
    
    if data.reference_text is not None:
        user.reference_text = data.reference_text
    
    db.commit()
    return {"message": "reference 수정 성공"}

# 감정 조회
@app.get("/api/emotion/read")
def get_latest_emotion(user_id: int, diary_id: int, db: Session = Depends(get_db)):
    # 가장 최근 생성된 emotion 검색
    user = db.query(models.User).filter(models.User.user_id == user_id).first()

    emotion = (
        db.query(models.Emotion)
        .filter(models.Emotion.user_id == user_id, models.Emotion.diary_id == diary_id)
        .order_by(models.Emotion.created_at.desc())
        .first()
    )

    if not emotion:
        raise HTTPException(status_code=404, detail="감정 기록이 없습니다.")
    
    # 반환할 데이터 구성을 딕셔너리로 만들어서 반환
    return {
        "emotion_id": emotion.emotion_id,
        "user_id": emotion.user_id,
        "diary_id": emotion.diary_id,
        "joy": emotion.joy,
        "sadness": emotion.sadness,
        "anger": emotion.anger,
        "fear": emotion.fear,
        "disgust": emotion.disgust,
        "anxiety": emotion.anxiety,
        "envy": emotion.envy,
        "bewilderment": emotion.bewilderment,
        "boredom": emotion.boredom,
        "created_at": emotion.created_at
    }

# 일기 이미지 생성
class ImageData(BaseModel):
    diary_id: int
    content: str
    created_at: datetime

@app.put("/api/diary/image/create")
def create_image(data: ImageData, db: Session = Depends(get_db)):
    # 기존 diary 찾기 (diary_id에 해당하는 일기)
    diary = db.query(models.Diary).filter(models.Diary.diary_id == data.diary_id).first()

    if not diary:
        raise HTTPException(status_code=404, detail="일기를 찾을 수 없습니다.")

    print(data.content)
    # 이미지 생성
    image_url, filename = generate_mone_pastel_image(data.content)
    
    # 기존 diary의 photo 필드를 새 URL로 수정
    diary.photo = image_url

    # 변경 내용 저장
    db.commit()
    db.refresh(diary)  # 선택적, 최신 상태 가져오기

    return {
        "message": "기록 성공",
        "URL": image_url,
        "FILENAME": filename
    }

# 감정들의 평균
# os.getenv('autogen_api_key')
def get_db_connection():
    # MySQL에 맞게 연결 정보 설정하기
    conn = mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),      # MySQL Username
        password=os.getenv('DB_PASSWORD'), # Password
        database=os.getenv('DB_DATABASE')   # 데이터베이스 이름
    )
    return conn

@app.get("/api/feeling/")
async def average_emotions():
    try:
        today = datetime.now()
        yesterday = today - timedelta(days=1)
        yesterday_start = datetime(yesterday.year, yesterday.month, yesterday.day, 0, 0, 0)
        yesterday_end = datetime(yesterday.year, yesterday.month, yesterday.day, 23, 59, 59)

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)  # dictionary=True로 설정하면 결과를 딕셔너리 형태로 받음

        query = """
        SELECT
            CAST(AVG(joy) AS UNSIGNED) AS joy_avg,
            CAST(AVG(sadness) AS UNSIGNED) AS sadness_avg,
            CAST(AVG(anger) AS UNSIGNED) AS anger_avg,
            CAST(AVG(fear) AS UNSIGNED) AS fear_avg,
            CAST(AVG(disgust) AS UNSIGNED) AS disgust_avg,
            CAST(AVG(anxiety) AS UNSIGNED) AS anxiety_avg,
            CAST(AVG(envy) AS UNSIGNED) AS envy_avg,
            CAST(AVG(bewilderment) AS UNSIGNED) AS bewilderment_avg,
            CAST(AVG(boredom) AS UNSIGNED) AS boredom_avg
        FROM EMOTIONS
        WHERE created_at BETWEEN %s AND %s;
        """
        
        cursor.execute(query, (yesterday_start, yesterday_end))
        result = cursor.fetchone()

        # Convert query result to a dictionary, ensuring integers are returned
        emotion_averages = {
            "joy": result["joy_avg"] or 0,
            "sadness": result["sadness_avg"] or 0,
            "anger": result["anger_avg"] or 0,
            "fear": result["fear_avg"] or 0,
            "disgust": result["disgust_avg"] or 0,
            "anxiety": result["anxiety_avg"] or 0,
            "envy": result["envy_avg"] or 0,
            "bewilderment": result["bewilderment_avg"] or 0,
            "boredom": result["boredom_avg"] or 0,
        }
        
        cursor.close()
        conn.close()

        return emotion_averages
    except Exception as e:
        return {"error": str(e)}

# 포미가 해주는 공감 한스푼 API
class PsyInput(BaseModel):
    user_id: int
    diary_id: int
    diary_text: str

@app.post("/api/psy/create")
async def create_psy(data: PsyInput, db:Session = Depends(get_db)):

    diary_text = data.diary_text
    fome_result = request_psy(diary_text).get('response')

    print(fome_result)
    print(type(fome_result))

    new_psy = models.Psy(
        user_id=data.user_id,  # 여기서 user_id는 특정 사용자로 넣거나, 요청받은 데이터와 연동
        diary_id=data.diary_id,
        comment=fome_result,  # 감정 또는 메시지 처리 로직 필요
    )
    db.add(new_psy)
    db.commit()
    db.refresh(new_psy)

    return {
        "Fome": fome_result,
    }

class ImageSettingData(BaseModel):
    user_id: int
    nation: str
    sex: str
    age: int

@app.put("/api/image/setting")
async def input_image_setting(data: ImageSettingData, db: Session = Depends(get_db)):
    existing_setting = db.query(models.ImageSetting).filter(models.ImageSetting.user_id == data.user_id).first()

    if existing_setting:
        # 이미 존재하면 업데이트
        existing_setting.nation = data.nation
        existing_setting.sex = data.sex
        existing_setting.age = data.age
        db.commit()
        db.refresh(existing_setting)
        return {"message": "설정이 업데이트 되었습니다.", "setting": existing_setting}
    else:
        # 없으면 새로 생성
        new_image_setting = models.ImageSetting(
            user_id=data.user_id,
            nation=data.nation,
            sex=data.sex,
            age=data.age
        )
        db.add(new_image_setting)
        db.commit()
        db.refresh(new_image_setting)
        return {"message": "새 설정이 저장되었습니다.", "setting": new_image_setting}

########## 테스트 ##########

@app.get("/test")
async def read_root():
    return {"message": "Hello, FastAPI"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port)


# source venv/bin/activate     
# uvicorn app:app --host 0.0.0.0 --port 8000
