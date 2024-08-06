FROM python:3.11-slim
WORKDIR /medbot
COPY . /medbot/
RUN pip install -r requirements.txt
EXPOSE 8080
CMD python main.py
