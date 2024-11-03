from flask import Flask, render_template, request, redirect, url_for, session, flash
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
import os
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException
import folium 

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Remplacez par une clé secrète sécurisée

# Configuration de la base de données
Base = declarative_base()
engine = create_engine('sqlite:///datay_data.db')
Session = sessionmaker(bind=engine)
db_session = Session()

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
# autres imports restent inchangés

# Ajoutez la route pour mettre à jour la carte en fonction des coordonnées
@app.route("/update_map", methods=["GET"])
def update_map():
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    zoom = request.args.get("zoom", type=int, default=12)
    
    map_contacts = folium.Map(location=[lat, lon], zoom_start=zoom)
    map_path = os.path.join("static", "map_dynamic.html")
    map_contacts.save(map_path)
    
    return jsonify({"map_path": map_path})


# Fonction pour charger les cantons et les rues depuis le fichier Excel
def load_cantons_and_streets():
    cantons_df = pd.read_excel('streets_by_canton_francophones.xlsx')
    cantons = cantons_df.columns.tolist()
    canton_streets = {canton: cantons_df[canton].dropna().tolist() for canton in cantons}
    return cantons, canton_streets

# Classe utilisateur
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)
    role = Column(String, nullable=False)
    contacts = relationship("Contact", back_populates="user")

# Classe contact
class Contact(Base):
    __tablename__ = 'contacts'
    id = Column(Integer, primary_key=True)
    prenom = Column(String)
    nom = Column(String)
    adresse = Column(String)
    telephone = Column(String)
    qualification = Column(String, nullable=True)
    traitement = Column(Boolean, default=False)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    user = relationship("User", back_populates="contacts")

# Créer les tables
Base.metadata.create_all(engine)

# Fonction de scraping sans géocodage
def scrape_street(canton, rue):
    options = webdriver.ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')

    driver = webdriver.Chrome(options=options)
    url = f'https://search.ch/tel/?strasse={rue.replace(" ", "%20")}&privat=20'
    driver.get(url)

    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    entries = WebDriverWait(driver, 10).until(
        EC.presence_of_all_elements_located((By.XPATH, "//li[contains(@class, 'tel-person')]//article"))
    )

    data = []
    for entry in entries:
        try:
            nom_complet = entry.find_element(By.XPATH, ".//h1").text
            adresse = entry.find_element(By.CLASS_NAME, "tel-address").text
            telephone = entry.find_element(By.XPATH, ".//a[contains(@class, 'tel-callable')]").get_attribute("href")
            telephone = telephone.replace("tel:", "") if telephone else ""
            
            data.append({
                "nom_complet": nom_complet,
                "adresse": adresse,
                "telephone": telephone
            })
        except NoSuchElementException:
            continue

    print("Données extraites :", data)
    save_to_db(data)
    return data

# Enregistrement dans la base de données avec association de user_id
def save_to_db(data):
    user_id = session.get("user_id")  # Associe les contacts au user_id de la session
    for item in data:
        contact = Contact(
            prenom=item['nom_complet'].split()[0],
            nom=' '.join(item['nom_complet'].split()[1:]),
            adresse=item['adresse'],
            telephone=item['telephone'],
            user_id=user_id  # Enregistre avec le user_id de l'utilisateur connecté
        )
        db_session.add(contact)
    db_session.commit()

# Route pour le tableau de bord
@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    map_path = create_default_map()
    contacts = db_session.query(Contact).filter_by(user_id=session["user_id"], traitement=False).all()
    print("Contacts chargés pour affichage :", contacts)

    cantons, canton_streets = load_cantons_and_streets()
    return render_template("dashboard.html", cantons=cantons, map_path=map_path, contacts=contacts, canton_streets=canton_streets)

# Fonction pour créer la carte par défaut avec un zoom spécifique pour le canton sélectionné
def create_default_map(canton=None):
    coordinates = [46.8182, 8.2275]
    zoom_start = 7

    canton_coords = {
        "Geneva": [46.2044, 6.1432],
        "Zurich": [47.3769, 8.5417],
        # Ajoutez les autres cantons avec leurs coordonnées
    }

    if canton in canton_coords:
        coordinates = canton_coords[canton]
        zoom_start = 12

    map_contacts = folium.Map(location=coordinates, zoom_start=zoom_start)
    map_path = os.path.join("static", "map_dashboard.html")
    map_contacts.save(map_path)
    return "map_dashboard.html"

# Route pour lancer le scraping
@app.route("/run_scrape", methods=["POST"])
def run_scrape():
    canton = request.form.get("canton")
    rue = request.form.get("rue")
    
    if canton and rue:
        data = scrape_street(canton, rue)
        flash(f"{len(data)} contacts extraits pour le canton : {canton}, rue : {rue}")
    else:
        flash("Veuillez sélectionner un canton et une rue pour lancer le scraping.")
    
    return redirect(url_for("dashboard"))

# Route pour qualifier et traiter un contact
@app.route("/qualify_contact/<int:contact_id>", methods=["POST"])
def qualify_contact(contact_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    contact = db_session.query(Contact).filter_by(id=contact_id, user_id=session["user_id"]).first()
    if contact:
        contact.qualification = request.form.get("qualification")
        contact.traitement = request.form.get("traitement") == "1"
        db_session.commit()
        flash("Le contact a été mis à jour avec succès.")
    else:
        flash("Contact introuvable ou accès non autorisé.")

    return redirect(url_for("dashboard"))

# Route pour afficher la carte des contacts
@app.route("/show_map", methods=["GET"])
def show_map():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))

    map_contacts = folium.Map(location=[46.8182, 8.2275], zoom_start=7)
    contacts = db_session.query(Contact).filter_by(user_id=user_id).all()
    for contact in contacts:
        if contact.latitude and contact.longitude:
            folium.Marker(
                location=[contact.latitude, contact.longitude],
                popup=f"{contact.prenom} {contact.nom} - {contact.adresse}"
            ).add_to(map_contacts)

    map_path = os.path.join("static", "map.html")
    map_contacts.save(map_path)

    return render_template("map.html")

# Route pour la page de connexion
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        user = db_session.query(User).filter_by(username=username, password=password).first()
        if user and user.password == password:
            session["user_id"] = user.id
            session["username"] = user.username
            session["role"] = user.role
            return redirect(url_for("dashboard"))
        else:
            flash("Nom d'utilisateur ou mot de passe incorrect")
    return render_template("login.html")

# Route pour ajouter un utilisateur (admin uniquement)
@app.route("/add_user", methods=["GET", "POST"])
def add_user():
    if "role" not in session or session["role"] != "admin":
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        role = request.form["role"]
        if db_session.query(User).filter_by(username=username).first():
            flash("Nom d'utilisateur déjà utilisé")
        else:
            new_user = User(username=username, password=password, role=role)
            db_session.add(new_user)
            db_session.commit()
            flash(f"Utilisateur {username} ajouté avec succès")
        return redirect(url_for("dashboard"))

    return render_template("add_user.html")
# Route pour gérer les utilisateurs (admin uniquement)
@app.route("/manage_users", methods=["GET", "POST"])
def manage_users():
    if "role" not in session or session["role"] != "admin":
        return redirect(url_for("dashboard"))  # Rediriger si non admin

    users = db_session.query(User).all()  # Récupérer tous les utilisateurs

    if request.method == "POST":
        # Ici, vous pouvez ajouter la logique pour créer ou modifier des utilisateurs
        username = request.form.get("username")
        password = request.form.get("password")
        role = request.form.get("role")

        if username and password:
            new_user = User(username=username, password=password, role=role)
            db_session.add(new_user)
            db_session.commit()
            flash(f"Utilisateur {username} ajouté avec succès")

    return render_template("manage_users.html", users=users)
    
@app.route("/delete_user/<int:user_id>", methods=["POST"])
def delete_user(user_id):
    if "role" not in session or session["role"] != "admin":
        return redirect(url_for("dashboard"))  # Rediriger si non admin

    user_to_delete = db_session.query(User).filter_by(id=user_id).first()
    if user_to_delete:
        db_session.delete(user_to_delete)
        db_session.commit()
        flash("Utilisateur supprimé avec succès.")
    else:
        flash("Utilisateur introuvable.")

    return redirect(url_for("manage_users"))

# Route pour se déconnecter
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False, port=5001)
