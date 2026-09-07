import requests
from bs4 import BeautifulSoup
import json
import time

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

def get_all_models(page=1):
    url = f"https://www.pornhub.com/pornstars?page={page}"
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    models = []
    
    for card in soup.find_all('li', class_='modelLi'):
        try:
            # ID модели из href
            href = card.find('a')['href']
            model_id = href.replace('/model/', '')
            
            # Имя
            name = card.find('img')['alt']
            
            # Фото
            img = card.find('img')
            photo = img.get('src') or img.get('data-thumb_url')
            
            # Количество видео
            videos = card.find('span', class_='videosNumber')
            views = card.find('span', class_='viewsNumber')
            
            models.append({
                'id': model_id,
                'name': name,
                'photo': photo,
                'videos': videos.text.strip() if videos else None,
                'views': views.text.strip() if views else None,
                'url': f"https://www.pornhub.com/model/{model_id}"
            })
            
        except Exception as e:
            print(f"Ошибка: {e}")
            continue
    
    return models

# Парсим все страницы
all_models = []

for page in range(1, 2):  # подбери кол-во страниц
    print(f"Парсим страницу {page}...")
    models = get_all_models(page)
    
    if not models:  # если страница пустая - стоп
        print(f"Стоп на странице {page}")
        break
        
    all_models.extend(models)
    time.sleep(1)  # пауза чтоб не забанили

# Сохраняем в JSON
with open('models.json', 'w', encoding='utf-8') as f:
    json.dump(all_models, f, ensure_ascii=False, indent=2)

print(f"Всего моделей: {len(all_models)}")