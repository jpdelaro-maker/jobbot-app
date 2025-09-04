@echo off
cd /d "C:\Users\Jean-Paul\Desktop\00-PERSO JPL\Job App"
call ".venv\Scripts\activate.bat"
python -m streamlit run app.py
pause
