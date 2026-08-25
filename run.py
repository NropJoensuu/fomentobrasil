from app import create_app

app = create_app(iniciar_agendador=True)

if __name__ == "__main__":
    app.run(debug=True)
