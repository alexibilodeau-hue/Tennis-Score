import json
import os
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

import db
import elo

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
TZ = ZoneInfo("America/Toronto")

DAYS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


def current_day_and_hour():
    now = datetime.now(TZ)
    return now.weekday(), now.hour


def format_range(rng) -> str:
    if not rng:
        return "non disponible"
    start, end = rng
    return f"{start}h-{end}h"


TIER_DEFS = [
    ("Debutant", discord.Color.green()),
    ("Intermediaire", discord.Color.gold()),
    ("Expert", discord.Color.red()),
]
TROUVER_PARTENAIRE_CATEGORY_NAME = "TROUVER PARTENAIRE"


def compute_tier(elo_value: float) -> str:
    if elo_value < 500:
        return "Debutant"
    if elo_value < 800:
        return "Intermediaire"
    return "Expert"


intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

ESPACE_PERSONNEL_CATEGORY_NAME = "ESPACE PERSONNEL"
DEMANDE_ACCES_CATEGORY_NAME = "DEMANDES D'ACCÈS"
MOD_ROLE_NAMES = {"Founder", "Modérateur"}
MEMBRE_ROLE_NAME = "Membre"


def mk_embed(title, description="", color=discord.Color.orange()):
    return discord.Embed(title=title, description=description, color=color)


async def get_or_create_personal_category(guild: discord.Guild) -> discord.CategoryChannel:
    for cat in guild.categories:
        if cat.name == ESPACE_PERSONNEL_CATEGORY_NAME:
            return cat
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True),
    }
    return await guild.create_category(ESPACE_PERSONNEL_CATEGORY_NAME, overwrites=overwrites)


async def get_or_create_personal_channel(member: discord.Member) -> discord.TextChannel:
    guild = member.guild
    channel_name = f"espace-{member.name}".lower()[:90]
    for ch in guild.text_channels:
        if ch.topic == f"personal-space:{member.id}":
            await ch.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
            await ensure_espace_action_panel(ch, member.id)
            return ch
    category = await get_or_create_personal_category(guild)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    channel = await guild.create_text_channel(
        channel_name, category=category, overwrites=overwrites, topic=f"personal-space:{member.id}"
    )
    db.ensure_player(member.id)
    embed = mk_embed(
        f"Bienvenue dans ton espace personnel, {member.display_name} !",
        "Ici, seul toi (et le bot) peux voir ce salon.\n\n"
        "Utilise `/profil` pour voir ton niveau, ton classement et tes stats a tout moment.",
        color=discord.Color.orange(),
    )
    await channel.send(embed=embed)
    await ensure_espace_action_panel(channel, member.id)
    return channel


def get_mod_roles(guild: discord.Guild):
    return [r for r in guild.roles if r.name in MOD_ROLE_NAMES]


DISCUSSIONS_PRIVEES_CATEGORY_NAME = "DISCUSSIONS PRIVÉES"


async def get_or_create_match_channel(guild: discord.Guild, user_a_id, user_b_id) -> discord.TextChannel:
    """Crée (ou retrouve) un salon privé de discussion entre 2 joueurs qui se sont trouvés
    via la recherche de partenaire. Seuls les deux joueurs + Founder/Modérateur y ont accès."""
    a, b = sorted([str(user_a_id), str(user_b_id)])
    topic = f"match-prive:{a}:{b}"
    existing = discord.utils.find(lambda c: c.topic == topic, guild.text_channels)
    if existing:
        return existing

    category = discord.utils.find(lambda c: c.name == DISCUSSIONS_PRIVEES_CATEGORY_NAME, guild.categories)
    if not category:
        overwrites_cat = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True),
        }
        for role in get_mod_roles(guild):
            overwrites_cat[role] = discord.PermissionOverwrite(view_channel=True)
        category = await guild.create_category(DISCUSSIONS_PRIVEES_CATEGORY_NAME, overwrites=overwrites_cat)

    member_a = guild.get_member(int(a)) or await guild.fetch_member(int(a))
    member_b = guild.get_member(int(b)) or await guild.fetch_member(int(b))

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        member_a: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        member_b: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    for role in get_mod_roles(guild):
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    name_a = member_a.name.lower()[:20]
    name_b = member_b.name.lower()[:20]
    channel_name = f"match-{name_a}-{name_b}"[:90]
    channel = await guild.create_text_channel(
        channel_name, category=category, overwrites=overwrites, topic=topic,
    )
    await channel.send(
        f"🎾 Salon privé pour organiser votre match, {member_a.mention} et {member_b.mention} !\n\n"
        "Discutez ici de l'heure, du terrain et de tout le reste pour vous organiser.\n\n"
        "Une fois le match joué, utilisez le bouton **📊 Entrer le résultat** ci-dessous "
        "(l'autre joueur devra confirmer ici même). Vous pouvez aussi annuler le match si vous ne jouez plus finalement.",
        view=MatchActionsView(int(a), int(b)),
    )
    return channel


class EntrerResultatModal(discord.ui.Modal, title="Entrer le résultat du match"):
    victoire = discord.ui.TextInput(label="As-tu gagné ? (Oui/Non)", placeholder="Oui ou Non", max_length=5, required=True)
    score = discord.ui.TextInput(label="Score", placeholder="Ex: 6-4 6-3", max_length=30, required=True)

    def __init__(self, reporter_id: int, opponent_id: int):
        super().__init__()
        self.reporter_id = reporter_id
        self.opponent_id = opponent_id

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.victoire.value).strip().lower()
        if raw not in ("oui", "non"):
            await interaction.response.send_message("Réponds par \"Oui\" ou \"Non\".", ephemeral=True)
            return

        winner_id = self.reporter_id if raw == "oui" else self.opponent_id
        score_value = str(self.score.value).strip()

        db.ensure_player(self.reporter_id)
        db.ensure_player(self.opponent_id)
        match_id = db.create_pending_match(self.reporter_id, self.opponent_id, winner_id, score_value)

        embed = mk_embed(
            "Résultat de match en attente de confirmation",
            f"<@{self.reporter_id}> déclare : **<@{winner_id}> gagne {score_value}**.\n\n"
            f"<@{self.opponent_id}>, confirme ou conteste ce résultat ci-dessous.",
            color=discord.Color.orange(),
        )
        view = ConfirmMatchView(match_id, self.opponent_id)
        await interaction.response.send_message(content=f"<@{self.opponent_id}>", embed=embed, view=view)


class EntrerResultatButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"match_resultat:(?P<a>[0-9]+):(?P<b>[0-9]+)",
):
    def __init__(self, player_a_id: int, player_b_id: int):
        super().__init__(
            discord.ui.Button(
                label="📊 Entrer le résultat",
                style=discord.ButtonStyle.success,
                custom_id=f"match_resultat:{player_a_id}:{player_b_id}",
            )
        )
        self.player_a_id = player_a_id
        self.player_b_id = player_b_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["a"]), int(match["b"]))

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id not in (self.player_a_id, self.player_b_id):
            await interaction.response.send_message("Seuls les deux joueurs de ce match peuvent entrer un résultat.", ephemeral=True)
            return
        opponent_id = self.player_b_id if interaction.user.id == self.player_a_id else self.player_a_id
        await interaction.response.send_modal(EntrerResultatModal(interaction.user.id, opponent_id))


class AnnulerMatchButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"match_annuler:(?P<a>[0-9]+):(?P<b>[0-9]+)",
):
    def __init__(self, player_a_id: int, player_b_id: int):
        super().__init__(
            discord.ui.Button(
                label="❌ Annuler le match",
                style=discord.ButtonStyle.danger,
                custom_id=f"match_annuler:{player_a_id}:{player_b_id}",
            )
        )
        self.player_a_id = player_a_id
        self.player_b_id = player_b_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["a"]), int(match["b"]))

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id not in (self.player_a_id, self.player_b_id):
            await interaction.response.send_message("Seuls les deux joueurs de ce match peuvent l'annuler.", ephemeral=True)
            return
        view = self.view
        for child in view.children:
            child.disabled = True
        await interaction.response.edit_message(view=view)
        await interaction.followup.send(f"❌ Le match a été annulé par {interaction.user.mention}.")


class MatchActionsView(discord.ui.View):
    def __init__(self, player_a_id: int, player_b_id: int):
        super().__init__(timeout=None)
        self.player_a_id = player_a_id
        self.player_b_id = player_b_id
        self.add_item(EntrerResultatButton(player_a_id, player_b_id))
        self.add_item(AnnulerMatchButton(player_a_id, player_b_id))


async def get_or_create_tier_roles(guild: discord.Guild) -> dict:
    roles = {}
    for name, color in TIER_DEFS:
        role = discord.utils.find(lambda r, n=name: r.name == n, guild.roles)
        if not role:
            role = await guild.create_role(name=name, color=color, mentionable=True, reason="Roles de niveau Elo")
        roles[name] = role
    return roles


async def sync_tier_role(guild: discord.Guild, user_id, elo_value: float, tier_roles: dict = None):
    if guild is None:
        return
    member = guild.get_member(int(user_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(user_id))
        except discord.NotFound:
            return
    if tier_roles is None:
        tier_roles = await get_or_create_tier_roles(guild)
    target_tier = compute_tier(elo_value)
    to_remove = [r for name, r in tier_roles.items() if name != target_tier and r in member.roles]
    if to_remove:
        await member.remove_roles(*to_remove, reason="Mise a jour du niveau Elo")
    if tier_roles[target_tier] not in member.roles:
        await member.add_roles(tier_roles[target_tier], reason="Mise a jour du niveau Elo")


async def ensure_partner_channels(guild: discord.Guild, tier_roles: dict):
    category = discord.utils.find(lambda c: c.name == TROUVER_PARTENAIRE_CATEGORY_NAME, guild.categories)
    if not category:
        category = await guild.create_category(TROUVER_PARTENAIRE_CATEGORY_NAME)

    for tier_name, role in tier_roles.items():
        channel_name = f"partenaire-{tier_name.lower()}"
        channel = discord.utils.find(lambda c, n=channel_name: c.name == n, guild.text_channels)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            role: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        for mod_role in get_mod_roles(guild):
            overwrites[mod_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        if not channel:
            channel = await guild.create_text_channel(
                channel_name, category=category, overwrites=overwrites,
                topic=f"Trouve des partenaires de niveau {tier_name}.",
            )
        else:
            if channel.category_id != category.id:
                await channel.edit(category=category)
            for target, overwrite in overwrites.items():
                await channel.set_permissions(target, overwrite=overwrite)

        history = [m async for m in channel.history(limit=1)]
        if not history:
            await channel.send(
                f"Ce salon est réservé aux membres de niveau **{tier_name}** (assigné automatiquement selon ton Elo).\n"
                f"Utilise `/trouver-partenaire` ici pour trouver d'autres joueurs de ton niveau."
            )


async def get_or_create_demande_category(guild: discord.Guild) -> discord.CategoryChannel:
    for cat in guild.categories:
        if cat.name == DEMANDE_ACCES_CATEGORY_NAME:
            return cat
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True),
    }
    for role in get_mod_roles(guild):
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    return await guild.create_category(DEMANDE_ACCES_CATEGORY_NAME, overwrites=overwrites)


async def get_or_create_demande_channel(member: discord.Member):
    guild = member.guild
    membre_role = discord.utils.find(lambda r: r.name == MEMBRE_ROLE_NAME, guild.roles)
    if membre_role and membre_role in member.roles:
        return None

    topic = f"demande-acces:{member.id}"
    for ch in guild.text_channels:
        if ch.topic == topic:
            await ch.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
            await ensure_demande_acces_panel(ch, member.id)
            return ch

    category = await get_or_create_demande_category(guild)
    channel_name = f"demande-{member.name}".lower()[:90]
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    for role in get_mod_roles(guild):
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    channel = await guild.create_text_channel(
        channel_name, category=category, overwrites=overwrites, topic=topic
    )
    await ensure_demande_acces_panel(channel, member.id)
    return channel


class CreerProfilButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="📝 Créer mon profil", style=discord.ButtonStyle.success, row=0, custom_id="demande_creer_profil")

    async def callback(self, interaction: discord.Interaction):
        db.ensure_player(interaction.user.id)
        p = db.get_player(interaction.user.id)
        if p and p["niveau_ntrp"]:
            await interaction.response.send_message(
                "Tu as déjà créé ton profil ! Utilise **✏️ Modifier mon profil** si tu veux changer quelque chose.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(ModifierProfilModal(interaction.user.id, actor_is_mod=False, require_all=True))


class ModifierProfilButtonDemande(discord.ui.Button):
    def __init__(self):
        super().__init__(label="✏️ Modifier mon profil", style=discord.ButtonStyle.secondary, row=0, custom_id="demande_modifier_profil")

    async def callback(self, interaction: discord.Interaction):
        db.ensure_player(interaction.user.id)
        await interaction.response.send_modal(ModifierProfilModal(interaction.user.id, actor_is_mod=is_mod(interaction.user)))


class DisponibiliteButtonDemande(discord.ui.Button):
    def __init__(self):
        super().__init__(label="📅 Mes disponibilités", style=discord.ButtonStyle.primary, row=1, custom_id="demande_dispo")

    async def callback(self, interaction: discord.Interaction):
        db.ensure_player(interaction.user.id)
        await interaction.response.send_modal(DisponibiliteModal(interaction.user.id))


class DemandeAccesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CreerProfilButton())
        self.add_item(ModifierProfilButtonDemande())
        self.add_item(DisponibiliteButtonDemande())


async def ensure_demande_acces_panel(channel: discord.TextChannel, user_id: int):
    """Envoie une fois par membre les explications + boutons pour rejoindre le serveur."""
    flag_key = f"demande_panel_v2_sent_{user_id}"
    if db.get_kv(flag_key):
        return
    member = channel.guild.get_member(user_id) if channel.guild else None
    display_name = member.display_name if member else str(user_id)
    embed = mk_embed(
        f"Bienvenue {display_name} !",
        "Voici comment rejoindre le serveur, en 3 étapes :\n\n"
        "**1️⃣ Crée ton profil** - clique sur **📝 Créer mon profil** ci-dessous (une seule fois). "
        "Un petit formulaire va s'ouvrir : ton niveau, ton objectif, ta préférence (simple/double) et ton secteur.\n\n"
        "**2️⃣ Remplis tes disponibilités** - clique sur **📅 Mes disponibilités** pour dire quels jours et à quelles heures tu peux jouer.\n\n"
        "**3️⃣ On analyse ta demande** - une fois ton profil confirmé, il s'affiche automatiquement ici pour que "
        "l'équipe (Founder/Modérateur) puisse le voir et valider ton accès.\n\n"
        "Tu peux aussi écrire un message pour te présenter (prénom, depuis quand tu joues, pourquoi tu veux rejoindre).\n\n"
        "Si tu veux changer une réponse plus tard (avant ou après validation), utilise **✏️ Modifier mon profil** - "
        "ce bouton est utilisable autant de fois que tu veux, contrairement à **📝 Créer mon profil** qui ne fonctionne qu'une fois.\n\n"
        "Seuls toi et l'équipe peuvent voir ce salon. Une fois ta demande validée, tu auras accès à tout le serveur.",
        color=discord.Color.orange(),
    )
    try:
        message = await channel.send(embed=embed, view=DemandeAccesView())
        try:
            await message.pin()
        except discord.HTTPException as e:
            print(f"[demande_panel] impossible d'épingler pour {user_id}: {e!r}", flush=True)
        db.set_kv(flag_key, "1")
    except Exception as e:
        print(f"[demande_panel] ERREUR pour {user_id}: {e!r}", flush=True)


async def announce_new_member(member: discord.Member):
    channel = discord.utils.find(lambda c: c.name == "bienvenue", member.guild.text_channels)
    if not channel:
        return
    embed = mk_embed(
        "Nouveau membre !",
        f"Bienvenue {member.mention} sur le serveur Tennis Sherbrooke ! 🎾\n\n"
        "Va te présenter dans ton salon de demande d'accès privé pour obtenir ton accès complet.",
        color=discord.Color.orange(),
    )
    await channel.send(embed=embed)


@bot.event
async def on_member_remove(member: discord.Member):
    print(f"[on_member_remove] {member} ({member.id}) a quitté le serveur {member.guild.id}", flush=True)
    if member.bot:
        return
    if GUILD_ID and member.guild.id != GUILD_ID:
        return
    try:
        role_ids = [r.id for r in member.roles if r.name != "@everyone"]
        print(f"[on_member_remove] rôles sauvegardés pour {member.id}: {role_ids}", flush=True)
        if role_ids:
            db.save_roles(member.id, role_ids)
    except Exception as e:
        print(f"[on_member_remove] ERREUR: {e!r}", flush=True)


async def remove_demande_access(member: discord.Member):
    topic = f"demande-acces:{member.id}"
    for ch in member.guild.text_channels:
        if ch.topic == topic:
            await ch.set_permissions(member, overwrite=None)
            print(f"[remove_demande_access] accès retiré pour {member.id} sur {ch.name}", flush=True)
            return


def has_membre_role(member: discord.Member) -> bool:
    return any(r.name == MEMBRE_ROLE_NAME for r in member.roles)


@bot.event
async def on_member_join(member: discord.Member):
    print(f"[on_member_join] {member} ({member.id}) a rejoint le serveur {member.guild.id}", flush=True)
    if member.bot:
        return
    if GUILD_ID and member.guild.id != GUILD_ID:
        return

    try:
        saved_role_ids = db.get_saved_roles(member.id)
        print(f"[on_member_join] rôles sauvegardés trouvés pour {member.id}: {saved_role_ids}", flush=True)
        if saved_role_ids:
            roles_to_restore = [member.guild.get_role(rid) for rid in saved_role_ids]
            roles_to_restore = [r for r in roles_to_restore if r is not None]
            if roles_to_restore:
                await member.add_roles(*roles_to_restore, reason="Restauration des rôles après re-entrée sur le serveur")
                print(f"[on_member_join] rôles restaurés: {[r.name for r in roles_to_restore]}", flush=True)

        if has_membre_role(member):
            await get_or_create_personal_channel(member)
            print(f"[on_member_join] espace personnel ok pour {member.id}", flush=True)
        else:
            await get_or_create_demande_channel(member)
            print(f"[on_member_join] salon demande ok pour {member.id}", flush=True)
        await announce_new_member(member)
        print(f"[on_member_join] annonce bienvenue envoyée pour {member.id}", flush=True)
    except Exception as e:
        print(f"[on_member_join] ERREUR: {e!r}", flush=True)
        raise


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if after.bot:
        return
    if GUILD_ID and after.guild.id != GUILD_ID:
        return
    if has_membre_role(before) or not has_membre_role(after):
        return
    try:
        await get_or_create_personal_channel(after)
        print(f"[on_member_update] espace personnel créé pour {after.id} (nouveau membre)", flush=True)
        await remove_demande_access(after)
    except Exception as e:
        print(f"[on_member_update] ERREUR: {e!r}", flush=True)


async def reconcile_all_members():
    """Revérifie tous les membres et corrige les salons/permissions manquants ou cassés.
    Sert de filet de sécurité si un évènement on_member_join/remove a été manqué
    (redémarrage du bot, coupure réseau, etc.)."""
    if not GUILD_ID:
        return
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    tier_roles = await get_or_create_tier_roles(guild)
    count = 0
    async for member in guild.fetch_members(limit=None):
        if member.bot:
            continue
        try:
            if has_membre_role(member):
                await get_or_create_personal_channel(member)
                await remove_demande_access(member)
                p = db.get_player(member.id)
                if p:
                    print(f"[debug-tier] {member.id} {member.name}: niveau_ntrp={p['niveau_ntrp']!r} elo={p['elo']} matches_played={p['matches_played']}", flush=True)
                    if p["matches_played"] == 0 and p["niveau_ntrp"] in NIVEAU_STARTING_ELO:
                        expected_elo = NIVEAU_STARTING_ELO[p["niveau_ntrp"]]
                        if p["elo"] != expected_elo:
                            db.set_elo(member.id, expected_elo)
                            p = db.get_player(member.id)
                    await sync_tier_role(guild, member.id, p["elo"], tier_roles=tier_roles)
            else:
                await get_or_create_demande_channel(member)
            count += 1
        except Exception as e:
            print(f"[reconcile] ERREUR pour {member} ({member.id}): {e!r}", flush=True)
    print(f"[reconcile] {count} membres vérifiés.", flush=True)


@tasks.loop(minutes=10)
async def reconcile_loop():
    await reconcile_all_members()


CHECKIN_WEEKDAY = 0  # Lundi
CHECKIN_HOUR = 18    # 18h, heure du Québec

DAYS_SHORT = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]

DAY_ABBREVIATIONS = {
    "lun": 0, "lundi": 0,
    "mar": 1, "mardi": 1,
    "mer": 2, "mercredi": 2,
    "jeu": 3, "jeudi": 3,
    "ven": 4, "vendredi": 4,
    "sam": 5, "samedi": 5,
    "dim": 6, "dimanche": 6,
}


class DisponibiliteModal(discord.ui.Modal, title="Mes disponibilités"):
    jours = discord.ui.TextInput(
        label="Jours (ex: Lun, Mer, Ven ou Tous)",
        placeholder="Lun, Mer, Ven",
        max_length=60,
        required=True,
    )
    heure_debut = discord.ui.TextInput(
        label="À partir de quelle heure ? (6-23)",
        placeholder="Ex: 18",
        max_length=2,
        required=True,
    )
    heure_fin = discord.ui.TextInput(
        label="Jusqu'à quelle heure ? (7-23)",
        placeholder="Ex: 21",
        max_length=2,
        required=True,
    )

    def __init__(self, user_id: int):
        super().__init__()
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        raw_jours = str(self.jours.value).strip().lower()
        if raw_jours in ("tous", "tout", "tous les jours"):
            day_indices = list(range(7))
        else:
            day_indices = []
            for part in raw_jours.split(","):
                part = part.strip()
                if not part:
                    continue
                if part not in DAY_ABBREVIATIONS:
                    await interaction.response.send_message(
                        f"Jour non reconnu : \"{part}\". Utilise Lun, Mar, Mer, Jeu, Ven, Sam, Dim (séparés par des virgules) ou \"Tous\".",
                        ephemeral=True,
                    )
                    return
                day_indices.append(DAY_ABBREVIATIONS[part])

        if not day_indices:
            await interaction.response.send_message("Indique au moins un jour.", ephemeral=True)
            return

        try:
            start_hour = int(str(self.heure_debut.value).strip())
            end_hour = int(str(self.heure_fin.value).strip())
        except ValueError:
            await interaction.response.send_message("Les heures doivent être des nombres (ex: 18).", ephemeral=True)
            return

        if not (6 <= start_hour <= 23) or not (7 <= end_hour <= 23) or end_hour <= start_hour:
            await interaction.response.send_message(
                "Heures invalides. Choisis une heure de début entre 6 et 23, et une heure de fin plus grande, jusqu'à 23.",
                ephemeral=True,
            )
            return

        day_set = set(day_indices)
        jours_fr = ", ".join(DAYS_FR[d] for d in sorted(day_set))
        view = ConfirmDisponibiliteView(self.user_id, day_set, start_hour, end_hour)
        await interaction.response.send_message(
            f"⚠️ **Attention** : ceci va **remplacer tout ton horaire actuel**.\n\n"
            f"Nouvel horaire proposé : **{jours_fr}** de **{start_hour}h à {end_hour}h**.\n"
            "Les autres jours (s'il y en avait) seront effacés.\n\n"
            "Clique sur **Confirmer** pour appliquer, ou ignore ce message pour annuler.",
            view=view,
            ephemeral=True,
        )


class ConfirmDisponibiliteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="✅ Confirmer", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        view: ConfirmDisponibiliteView = self.view
        db.ensure_player(view.user_id)
        for day in range(7):
            if day in view.day_set:
                db.set_day_range(view.user_id, day, view.start_hour, view.end_hour)
            else:
                db.clear_day_range(view.user_id, day)
        jours_fr = ", ".join(DAYS_FR[d] for d in sorted(view.day_set))
        for child in view.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"✅ Ton horaire a été **remplacé** par : **{jours_fr}** de **{view.start_hour}h à {view.end_hour}h**.\n"
            "Refais `/disponibilite` pour tout remettre à jour.",
            view=view,
        )


class AnnulerDisponibiliteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Annuler", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        view: ConfirmDisponibiliteView = self.view
        for child in view.children:
            child.disabled = True
        await interaction.response.edit_message(content="Annulé, rien n'a été changé.", view=view)


class ConfirmDisponibiliteView(discord.ui.View):
    def __init__(self, user_id: int, day_set: set, start_hour: int, end_hour: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.day_set = day_set
        self.start_hour = start_hour
        self.end_hour = end_hour
        self.add_item(ConfirmDisponibiliteButton())
        self.add_item(AnnulerDisponibiliteButton())


@bot.tree.command(name="disponibilite", description="Indique tes disponibilités via un formulaire (REMPLACE tout ton horaire actuel).")
async def disponibilite(interaction: discord.Interaction):
    db.ensure_player(interaction.user.id)
    await interaction.response.send_modal(DisponibiliteModal(interaction.user.id))


class OuvrirDisponibiliteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="📅 Mettre à jour mes disponibilités", style=discord.ButtonStyle.primary, custom_id="ouvrir_dispo")

    async def callback(self, interaction: discord.Interaction):
        db.ensure_player(interaction.user.id)
        await interaction.response.send_modal(DisponibiliteModal(interaction.user.id))


class OuvrirDisponibiliteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(OuvrirDisponibiliteButton())


async def send_weekly_checkins():
    """Envoie chaque semaine un message très simple (boutons à cliquer) en privé à chaque membre
    pour qu'il indique ses jours disponibles. Si les DM sont fermés, le message est envoyé dans
    son espace personnel à la place. Vérifie last_checkin pour n'envoyer qu'une fois par semaine."""
    if not GUILD_ID:
        return
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    today_str = datetime.now(TZ).date().isoformat()
    async for member in guild.fetch_members(limit=None):
        if member.bot or not has_membre_role(member):
            continue
        p = db.get_player(member.id)
        if p and p["last_checkin"] == today_str:
            continue
        try:
            db.ensure_player(member.id)
            view = OuvrirDisponibiliteView()
            content = (
                "\U0001F3BE Nouvelle semaine de tennis ! Quels jours peux-tu jouer cette semaine ?\n\n"
                "Clique sur le bouton ci-dessous pour ouvrir le petit formulaire (jours + heures). "
                "Pas besoin de taper de commande !"
            )
            try:
                await member.send(content, view=view)
            except discord.Forbidden:
                channel = await get_or_create_personal_channel(member)
                await channel.send(content, view=view)
            db.set_last_checkin(member.id, today_str)
        except Exception as e:
            print(f"[checkin] ERREUR pour {member} ({member.id}): {e!r}", flush=True)


@tasks.loop(minutes=60)
async def weekly_checkin_loop():
    now = datetime.now(TZ)
    if now.weekday() == CHECKIN_WEEKDAY and now.hour == CHECKIN_HOUR:
        await send_weekly_checkins()


@bot.event
async def on_ready():
    db.init_db()

    if not getattr(bot, "_persistent_views_registered", False):
        bot.add_view(EspaceActionsView())
        bot.add_view(DemandeAccesView())
        bot.add_view(OuvrirDisponibiliteView())
        bot.add_dynamic_items(
            EntrerResultatButton, AnnulerMatchButton, AccepterMatchButton, PartantButton, EventJoinButton,
        )
        bot._persistent_views_registered = True

    guild_ref = discord.Object(id=GUILD_ID) if GUILD_ID else None
    if guild_ref:
        bot.tree.copy_global_to(guild=guild_ref)
        await bot.tree.sync(guild=guild_ref)
    else:
        await bot.tree.sync()
    print(f"Connecte en tant que {bot.user} - commandes synchronisees.")

    if GUILD_ID:
        guild = bot.get_guild(GUILD_ID)
        if guild:
            try:
                tier_roles = await get_or_create_tier_roles(guild)
                await ensure_partner_channels(guild, tier_roles)
            except Exception as e:
                print(f"[ensure_partner_channels] ERREUR: {e!r}", flush=True)

    await reconcile_all_members()

    if not reconcile_loop.is_running():
        reconcile_loop.start()
    if not weekly_checkin_loop.is_running():
        weekly_checkin_loop.start()
    if not monthly_rewards_loop.is_running():
        monthly_rewards_loop.start()
    if not smart_notifications_loop.is_running():
        smart_notifications_loop.start()
    if not friday_event_loop.is_running():
        friday_event_loop.start()


# ---------- PROFIL ----------

OBJECTIF_CHOICES = [
    app_commands.Choice(name="Loisir", value="loisir"),
    app_commands.Choice(name="Progression", value="progression"),
    app_commands.Choice(name="Compétition", value="competition"),
]

PREFERENCE_CHOICES = [
    app_commands.Choice(name="Simple", value="simple"),
    app_commands.Choice(name="Double", value="double"),
    app_commands.Choice(name="Les deux", value="les_deux"),
]

OUI_NON_CHOICES = [
    app_commands.Choice(name="Oui", value="oui"),
    app_commands.Choice(name="Non", value="non"),
]

SECTEUR_CHOICES = [
    app_commands.Choice(name="Nord", value="Nord"),
    app_commands.Choice(name="Sud", value="Sud"),
    app_commands.Choice(name="Est", value="Est"),
    app_commands.Choice(name="Rock Forest", value="Rock Forest"),
    app_commands.Choice(name="Lennoxville", value="Lennoxville"),
    app_commands.Choice(name="Magog", value="Magog"),
]


@bot.tree.command(name="mon-espace", description="Crée (ou retrouve) ton espace personnel privé sur le serveur.")
async def mon_espace(interaction: discord.Interaction):
    if not has_membre_role(interaction.user):
        await interaction.response.send_message(
            "Ton espace personnel sera créé automatiquement une fois que tu seras validé comme Membre.", ephemeral=True
        )
        return
    channel = await get_or_create_personal_channel(interaction.user)
    await interaction.response.send_message(f"Ton espace personnel : {channel.mention}", ephemeral=True)


def build_profile_embed(target) -> discord.Embed:
    db.ensure_player(target.id)
    p = db.get_player(target.id)

    ranges = db.get_all_ranges(target.id)
    dispo_summary = (
        ", ".join(f"{DAYS_FR[d]} {format_range(r)}" for d, r in sorted(ranges.items())) if ranges else "non configurée"
    )

    now_iso = datetime.now(TZ).isoformat()
    instant_active = bool(p["instant_until"]) and p["instant_until"] > now_iso
    instant_text = "Oui (disponible maintenant !)" if instant_active else "Non"

    embed = mk_embed(f"Profil de {target.display_name}", description=target.mention, color=discord.Color.orange())
    embed.add_field(name="Niveau (NTRP)", value=p["niveau_ntrp"] or "non défini", inline=True)
    embed.add_field(name="Niveau (Elo)", value=str(round(p["elo"])), inline=True)
    embed.add_field(name="Âge", value=str(p["age"]) if p["age"] else "non défini", inline=True)
    embed.add_field(name="Matchs joués", value=str(p["matches_played"]), inline=True)
    embed.add_field(name="Victoires / Défaites", value=f"{p['wins']} / {p['losses']}", inline=True)
    embed.add_field(name="Série de victoires", value=str(p["win_streak"]), inline=True)
    embed.add_field(name="Objectif", value=p["objectif"] or "non défini", inline=True)
    embed.add_field(name="Préférence", value=p["preference"] or "non définie", inline=True)
    embed.add_field(name="Secteur", value=p["secteur"] or "non défini", inline=True)
    embed.add_field(name="Jours disponibles (`/disponibilite`)", value=dispo_summary, inline=False)
    embed.add_field(name="Dispo immédiate (`/dispo-maintenant`)", value=instant_text, inline=False)

    reliability = db.get_reliability_pct(target.id)
    embed.add_field(
        name="Fiabilité (présence aux matchs)",
        value=f"{reliability}% de présence confirmée" if reliability is not None else "pas encore évaluée",
        inline=False,
    )

    rep = db.get_reputation(target.id)
    if rep and rep["total_ratings"] > 0:
        rep_text = (
            f"Ponctuel: {rep['ponctuel']} - Agréable: {rep['agreable']} - "
            f"Bon niveau: {rep['bon_niveau']} - Rejouerais: {rep['rejouerais']} "
            f"(sur {rep['total_ratings']} évaluation(s))"
        )
    else:
        rep_text = "Pas encore d'évaluation"
    embed.add_field(name="Réputation", value=rep_text, inline=False)

    badges = compute_badges(target.id)
    embed.add_field(name="Badges (`/badges`)", value=", ".join(badges) if badges else "aucun pour le moment", inline=False)

    if target.avatar:
        embed.set_thumbnail(url=target.avatar.url)
    return embed


@bot.tree.command(name="profil", description="Affiche ton profil joueur (ou celui d'un autre membre).")
@app_commands.describe(membre="Le membre dont tu veux voir le profil (optionnel)")
async def profil(interaction: discord.Interaction, membre: discord.Member = None):
    target = membre or interaction.user
    await interaction.response.send_message(embed=build_profile_embed(target))


def is_mod(member: discord.Member) -> bool:
    return any(r.name in MOD_ROLE_NAMES for r in member.roles)


NIVEAU_OPTIONS = [
    discord.SelectOption(label="Débutant complet", value="Débutant complet"),
    discord.SelectOption(label="2.5 - 3.0", value="2.5 - 3.0"),
    discord.SelectOption(label="3.5", value="3.5"),
    discord.SelectOption(label="4.0", value="4.0"),
    discord.SelectOption(label="4.5 et plus", value="4.5 et plus"),
]

# Elo de depart deduit du niveau NTRP declare, applique tant que le joueur n'a pas encore
# joue de match (ensuite l'Elo reel pris le relais et n'est plus touche par le profil).
NIVEAU_STARTING_ELO = {
    "Débutant complet": 250,
    "2.5 - 3.0": 450,
    "3.5": 650,
    "4.0": 850,
    "4.5 et plus": 1050,
}

def _normalize_text(value: str) -> str:
    """Enleve les accents et met en minuscule, pour comparer du texte tape sans accent."""
    decomposed = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _match_choice(raw_value: str, valid_values: list) -> str:
    """Compare raw_value (insensible a la casse/espaces/accents) aux valeurs valides. Retourne la valeur exacte ou None."""
    raw = _normalize_text(raw_value)
    for v in valid_values:
        if _normalize_text(v) == raw:
            return v
    return None


NIVEAU_VALUES = [o.value for o in NIVEAU_OPTIONS]
OBJECTIF_VALUES = [c.value for c in OBJECTIF_CHOICES]
OBJECTIF_NAMES = {c.value: c.name for c in OBJECTIF_CHOICES}
PREFERENCE_VALUES = [c.value for c in PREFERENCE_CHOICES]
PREFERENCE_NAMES = {c.value: c.name for c in PREFERENCE_CHOICES}
SECTEUR_VALUES = [c.value for c in SECTEUR_CHOICES]


class ModifierProfilModal(discord.ui.Modal, title="Modifier mon profil"):
    niveau_ntrp = discord.ui.TextInput(
        label="Niveau NTRP (Débutant complet / 2.5-3.0 / 3.5 / 4.0 / 4.5+)",
        placeholder="Ex: 3.5",
        max_length=20,
        required=False,
    )
    objectif = discord.ui.TextInput(
        label="Objectif (Loisir / Progression / Compétition)",
        placeholder="Ex: Progression",
        max_length=20,
        required=False,
    )
    preference = discord.ui.TextInput(
        label="Préférence (Simple / Double / Les deux)",
        placeholder="Ex: Les deux",
        max_length=20,
        required=False,
    )
    secteur = discord.ui.TextInput(
        label="Secteur (Nord/Sud/Est/Rock Forest/Lennoxville/Magog)",
        placeholder="Ex: Rock Forest",
        max_length=20,
        required=False,
    )
    age = discord.ui.TextInput(label="Âge", placeholder="Ex: 28", max_length=3, required=False)

    def __init__(self, target_id: int, actor_is_mod: bool, require_all: bool = False):
        super().__init__()
        self.target_id = target_id
        self.actor_is_mod = actor_is_mod
        self.is_creation = require_all
        if require_all:
            self.title = "Créer mon profil"
            self.niveau_ntrp.required = True
            self.objectif.required = True
            self.preference.required = True
            self.secteur.required = True
        p = db.get_player(target_id)
        if p:
            self.niveau_ntrp.default = p["niveau_ntrp"] or ""
            self.objectif.default = OBJECTIF_NAMES.get(p["objectif"], "")
            self.preference.default = PREFERENCE_NAMES.get(p["preference"], "")
            self.secteur.default = p["secteur"] or ""
            self.age.default = str(p["age"]) if p["age"] else ""

    async def on_submit(self, interaction: discord.Interaction):
        errors = []
        niveau_value, objectif_value, preference_value, secteur_value, age_value = None, None, None, None, None

        raw = str(self.niveau_ntrp.value).strip()
        if raw:
            niveau_value = _match_choice(raw, NIVEAU_VALUES)
            if niveau_value is None:
                errors.append(f"Niveau invalide : \"{raw}\" (utilise Débutant complet, 2.5 - 3.0, 3.5, 4.0 ou 4.5 et plus).")

        raw = str(self.objectif.value).strip()
        if raw:
            objectif_value = next((v for v, n in OBJECTIF_NAMES.items() if _normalize_text(n) == _normalize_text(raw)), None)
            if objectif_value is None:
                errors.append(f"Objectif invalide : \"{raw}\" (utilise Loisir, Progression ou Compétition).")

        raw = str(self.preference.value).strip()
        if raw:
            preference_value = next((v for v, n in PREFERENCE_NAMES.items() if _normalize_text(n) == _normalize_text(raw)), None)
            if preference_value is None:
                errors.append(f"Préférence invalide : \"{raw}\" (utilise Simple, Double ou Les deux).")

        raw = str(self.secteur.value).strip()
        if raw:
            secteur_value = _match_choice(raw, SECTEUR_VALUES)
            if secteur_value is None:
                errors.append(f"Secteur invalide : \"{raw}\" (utilise Nord, Sud, Est, Rock Forest, Lennoxville ou Magog).")

        raw = str(self.age.value).strip()
        if raw:
            try:
                age_value = int(raw)
            except ValueError:
                errors.append("L'âge doit être un nombre.")

        if errors:
            await interaction.response.send_message("\n".join(errors), ephemeral=True)
            return

        changes = []
        if niveau_value is not None:
            changes.append(f"Niveau : **{niveau_value}**")
        if objectif_value is not None:
            changes.append(f"Objectif : **{OBJECTIF_NAMES[objectif_value]}**")
        if preference_value is not None:
            changes.append(f"Préférence : **{PREFERENCE_NAMES[preference_value]}**")
        if secteur_value is not None:
            changes.append(f"Secteur : **{secteur_value}**")
        if age_value is not None:
            changes.append(f"Âge : **{age_value}**")

        if not changes:
            await interaction.response.send_message("Tu n'as rien rempli, rien n'a été changé.", ephemeral=True)
            return

        view = ConfirmProfilView(self.target_id, niveau_value, objectif_value, preference_value, secteur_value, age_value, self.actor_is_mod)
        note = (
            "\n\n📅 N'oublie pas de remplir aussi tes **disponibilités** avec le bouton du salon une fois ton profil confirmé."
            if self.is_creation else ""
        )
        await interaction.response.send_message(
            "⚠️ **Attention** : ceci va **remplacer** les champs que tu as remplis dans ton profil.\n\n"
            + "\n".join(changes)
            + "\n\nClique sur **Confirmer** pour appliquer, ou ignore ce message pour annuler."
            + note,
            view=view,
            ephemeral=True,
        )


class ForcerEloModal(discord.ui.Modal, title="Forcer l'Elo"):
    elo_value = discord.ui.TextInput(label="Nouvel Elo", placeholder="Ex: 600", max_length=5, required=True)

    def __init__(self, target_id: int):
        super().__init__()
        self.target_id = target_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            new_elo = float(str(self.elo_value.value).strip())
        except ValueError:
            await interaction.response.send_message("L'Elo doit être un nombre.", ephemeral=True)
            return
        db.set_elo(self.target_id, new_elo)
        await sync_tier_role(interaction.guild, self.target_id, new_elo)
        await interaction.response.send_message(f"Elo forcé à {round(new_elo)}.", ephemeral=True)


class ForcerEloButton(discord.ui.Button):
    def __init__(self, target_id: int):
        super().__init__(label="Forcer l'Elo (Founder/Mod)", style=discord.ButtonStyle.danger)
        self.target_id = target_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ForcerEloModal(self.target_id))


class ConfirmProfilButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="✅ Confirmer", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        view: ConfirmProfilView = self.view
        db.update_player_profile(
            view.target_id,
            niveau_ntrp=view.niveau_value,
            objectif=view.objectif_value,
            preference=view.preference_value,
            secteur=view.secteur_value,
            age=view.age_value,
        )
        if view.niveau_value is not None:
            p = db.get_player(view.target_id)
            if p and p["matches_played"] == 0:
                starting_elo = NIVEAU_STARTING_ELO.get(view.niveau_value)
                if starting_elo is not None and starting_elo != p["elo"]:
                    db.set_elo(view.target_id, starting_elo)
                    if interaction.guild:
                        await sync_tier_role(interaction.guild, view.target_id, starting_elo)

        for child in view.children:
            child.disabled = True
        await interaction.response.edit_message(content="✅ Ton profil a été mis à jour !", view=view)

        try:
            target_member = interaction.guild.get_member(view.target_id) if interaction.guild else None
            target_user = target_member or await bot.fetch_user(view.target_id)
            await interaction.channel.send(
                f"📋 Profil mis à jour par <@{view.target_id}> (visible par l'équipe) :",
                embed=build_profile_embed(target_user),
            )
        except Exception as e:
            print(f"[ConfirmProfilButton] impossible de publier le profil: {e!r}", flush=True)


class AnnulerProfilButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Annuler", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        view: ConfirmProfilView = self.view
        for child in view.children:
            child.disabled = True
        await interaction.response.edit_message(content="Annulé, rien n'a été changé.", view=view)


class ConfirmProfilView(discord.ui.View):
    def __init__(self, target_id, niveau_value, objectif_value, preference_value, secteur_value, age_value, actor_is_mod):
        super().__init__(timeout=300)
        self.target_id = target_id
        self.niveau_value = niveau_value
        self.objectif_value = objectif_value
        self.preference_value = preference_value
        self.secteur_value = secteur_value
        self.age_value = age_value
        self.add_item(ConfirmProfilButton())
        self.add_item(AnnulerProfilButton())
        if actor_is_mod:
            self.add_item(ForcerEloButton(target_id))


@bot.tree.command(name="modifier-profil", description="Met à jour ton profil via un formulaire (ou celui d'un autre membre si Founder/Mod).")
@app_commands.describe(membre="Founder/Modérateur seulement : le membre dont tu veux modifier le profil")
async def modifier_profil(interaction: discord.Interaction, membre: discord.Member = None):
    if membre is not None and membre.id != interaction.user.id and not is_mod(interaction.user):
        await interaction.response.send_message(
            "Seuls les Founder/Modérateur peuvent modifier le profil d'un autre membre.", ephemeral=True
        )
        return

    target = membre or interaction.user
    db.ensure_player(target.id)
    await interaction.response.send_modal(ModifierProfilModal(target.id, actor_is_mod=is_mod(interaction.user)))


RECHERCHE_CHANNEL_NAME = "recherche-partenaire"


async def get_or_create_recherche_channel(guild: discord.Guild) -> discord.TextChannel:
    channel = discord.utils.find(lambda c: c.name == RECHERCHE_CHANNEL_NAME, guild.text_channels)
    if channel:
        return channel
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    membre_role = discord.utils.find(lambda r: r.name == MEMBRE_ROLE_NAME, guild.roles)
    if membre_role:
        overwrites[membre_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False)
    for mod_role in get_mod_roles(guild):
        overwrites[mod_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    return await guild.create_text_channel(
        RECHERCHE_CHANNEL_NAME, overwrites=overwrites,
        topic="Annonces publiques de joueurs qui cherchent une partie maintenant.",
    )


class PartantButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"partant:(?P<author_id>[0-9]+)",
):
    def __init__(self, author_id: int):
        super().__init__(
            discord.ui.Button(
                label="🎾 Je suis partant !",
                style=discord.ButtonStyle.success,
                custom_id=f"partant:{author_id}",
            )
        )
        self.author_id = author_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["author_id"]))

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id == self.author_id:
            await interaction.response.send_message(
                "C'est toi qui cherches un partenaire - attends qu'un autre membre clique !", ephemeral=True
            )
            return

        match_channel = await get_or_create_match_channel(interaction.guild, self.author_id, interaction.user.id)
        await interaction.response.send_message(
            f"{interaction.user.mention} est partant avec <@{self.author_id}> ! 🎾 "
            f"Rendez-vous dans {match_channel.mention} pour vous organiser."
        )
        author = interaction.client.get_user(self.author_id)
        if author:
            try:
                await author.send(
                    f"{interaction.user.display_name} a cliqué sur ton annonce \"Je cherche une partie\" ! "
                    f"Un salon privé {match_channel.mention} a été créé pour vous organiser."
                )
            except discord.Forbidden:
                pass


class PartantView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=10800)
        self.add_item(PartantButton(author_id))


class AccepterMatchButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"accepter_match:(?P<author_id>[0-9]+)",
):
    def __init__(self, author_id: int):
        super().__init__(
            discord.ui.Button(
                label="✅ J'accepte la partie !",
                style=discord.ButtonStyle.success,
                custom_id=f"accepter_match:{author_id}",
            )
        )
        self.author_id = author_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["author_id"]))

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id == self.author_id:
            await interaction.response.send_message(
                "C'est toi qui cherches un partenaire - attends que quelqu'un d'autre accepte !", ephemeral=True
            )
            return

        match_channel = await get_or_create_match_channel(interaction.guild, self.author_id, interaction.user.id)
        await interaction.response.send_message(
            f"{interaction.user.mention} accepte la partie avec <@{self.author_id}> ! 🎾 "
            f"Rendez-vous dans {match_channel.mention} pour vous organiser."
        )
        author = interaction.client.get_user(self.author_id)
        if author:
            try:
                await author.send(
                    f"{interaction.user.display_name} a accepté ta recherche de partenaire ! "
                    f"Un salon privé {match_channel.mention} a été créé pour vous organiser."
                )
            except discord.Forbidden:
                pass


class RechercherPartenaireView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=None)
        self.add_item(AccepterMatchButton(author_id))


@bot.tree.command(name="recherche-partenaire", description="Publie une annonce pour trouver un partenaire de tennis (les autres cliquent pour accepter).")
@app_commands.describe(message="Un mot pour préciser ta recherche (optionnel, ex: double samedi matin)")
async def recherche_partenaire(interaction: discord.Interaction, message: str = None):
    if not interaction.guild:
        await interaction.response.send_message("Cette commande doit être utilisée sur le serveur.", ephemeral=True)
        return

    db.ensure_player(interaction.user.id)
    p = db.get_player(interaction.user.id)
    niveau = p["niveau_ntrp"] if p and p["niveau_ntrp"] else f"Elo {round(p['elo'])}" if p else "non défini"
    secteur = p["secteur"] if p and p["secteur"] else "non défini"

    description = (
        f"{interaction.user.mention} cherche un partenaire de tennis ! 🎾\n\n"
        f"Niveau : {niveau}\n"
        f"Secteur : {secteur}"
    )
    if message:
        description += f"\n\n💬 {message}"
    description += "\n\nClique ci-dessous pour accepter la partie !"

    embed = mk_embed("🎾 Recherche de partenaire", description, color=discord.Color.orange())

    try:
        channel = await get_or_create_recherche_channel(interaction.guild)
    except Exception:
        channel = interaction.channel

    await channel.send(embed=embed, view=RechercherPartenaireView(interaction.user.id))
    await interaction.response.send_message(
        f"Ton annonce a été publiée dans {channel.mention} ! On te préviendra dès qu'un membre l'accepte.",
        ephemeral=True,
    )


@bot.tree.command(name="dispo-maintenant", description="Annonce que tu es disponible pour jouer dans les prochaines heures.")
@app_commands.choices(
    etat=[
        app_commands.Choice(name="Je suis dispo maintenant (3h)", value="on"),
        app_commands.Choice(name="Je ne suis plus dispo", value="off"),
    ]
)
async def dispo_maintenant(interaction: discord.Interaction, etat: app_commands.Choice[str]):
    if etat.value == "on":
        until = datetime.now(TZ) + timedelta(hours=3)
        db.set_instant_available(interaction.user.id, until.isoformat())
        await interaction.response.send_message(
            "Tu es marqué disponible pour les 3 prochaines heures. "
            "Les autres peuvent te trouver avec `/trouver-partenaire` (Maintenant).",
            ephemeral=True,
        )
        if interaction.guild:
            try:
                channel = await get_or_create_recherche_channel(interaction.guild)
                p = db.get_player(interaction.user.id)
                niveau = p["niveau_ntrp"] if p and p["niveau_ntrp"] else f"Elo {round(p['elo'])}" if p else "non défini"
                embed = mk_embed(
                    "🎾 Je cherche une partie maintenant !",
                    f"{interaction.user.mention} est disponible pour jouer dans les **3 prochaines heures**.\n"
                    f"Niveau : {niveau}\n\nClique ci-dessous si tu es partant !",
                    color=discord.Color.orange(),
                )
                await channel.send(embed=embed, view=PartantView(interaction.user.id))
            except Exception as e:
                print(f"[dispo-maintenant] annonce publique impossible: {e!r}", flush=True)
    else:
        db.clear_instant_available(interaction.user.id)
        await interaction.response.send_message("Disponibilité immédiate désactivée.", ephemeral=True)


# ---------- RECHERCHE DE PARTENAIRE ----------

QUAND_CHOICES = [
    app_commands.Choice(name="Maintenant", value="maintenant"),
    app_commands.Choice(name="Aujourd'hui", value="aujourdhui"),
    app_commands.Choice(name="Demain", value="demain"),
] + [app_commands.Choice(name=day, value=f"jour_{i}") for i, day in enumerate(DAYS_FR)]


def compute_compatibility(me_row, other_row) -> int:
    """Score indicatif sur 100 combinant niveau, secteur, objectif, disponibilités communes et nouveauté du partenaire."""
    score = 0.0

    elo_gap = abs((me_row["elo"] or 0) - (other_row["elo"] or 0))
    score += max(0, 30 - elo_gap / 20)  # jusqu'à 30 pts si niveau très proche

    if me_row["secteur"] and me_row["secteur"] == other_row["secteur"]:
        score += 20

    if me_row["objectif"] and me_row["objectif"] == other_row["objectif"]:
        score += 15

    me_ranges = db.get_all_ranges(me_row["user_id"])
    other_ranges = db.get_all_ranges(other_row["user_id"])
    common_days = 0
    for day, (s1, e1) in me_ranges.items():
        if day in other_ranges:
            s2, e2 = other_ranges[day]
            if s1 < e2 and s2 < e1:
                common_days += 1
    score += min(25, common_days * 8)

    if not db.has_played_before(me_row["user_id"], other_row["user_id"]):
        score += 10  # bonus pour découvrir un nouveau partenaire

    return min(100, round(score))


@bot.tree.command(name="trouver-partenaire", description="Trouve des membres disponibles pour jouer (par défaut : maintenant).")
@app_commands.describe(
    quand="Quand veux-tu jouer ? (par défaut: maintenant)",
    preference="Filtrer par simple ou double (optionnel)",
    secteur="Filtrer par secteur (optionnel)",
)
@app_commands.choices(quand=QUAND_CHOICES, preference=PREFERENCE_CHOICES, secteur=SECTEUR_CHOICES)
async def trouver_partenaire(
    interaction: discord.Interaction,
    quand: app_commands.Choice[str] = None,
    preference: app_commands.Choice[str] = None,
    secteur: app_commands.Choice[str] = None,
):
    pref = preference.value if preference else None
    sect = secteur.value if secteur else None
    exclude = interaction.user.id
    db.ensure_player(exclude)
    me_row = db.get_player(exclude)
    quand_value = quand.value if quand else "maintenant"

    if quand_value == "maintenant":
        day_index, hour = current_day_and_hour()
        recurring = db.search_recurring(day_index, hour, exclude_user_id=exclude, preference=pref, secteur=sect)
        instant = db.search_instant(datetime.now(TZ).isoformat(), exclude_user_id=exclude, preference=pref, secteur=sect)
        seen, rows = set(), []
        for row in list(instant) + list(recurring):
            if row["user_id"] not in seen:
                seen.add(row["user_id"])
                rows.append(row)
        label = f"Maintenant ({DAYS_FR[day_index]}, {hour}h)"
    else:
        if quand_value == "aujourdhui":
            day_index = datetime.now(TZ).weekday()
        elif quand_value == "demain":
            day_index = (datetime.now(TZ).weekday() + 1) % 7
        else:
            day_index = int(quand_value.split("_")[1])
        rows = db.search_recurring(day_index, hour=None, exclude_user_id=exclude, preference=pref, secteur=sect)
        label = f"{DAYS_FR[day_index]}"

    rows = list(rows)
    rows.sort(key=lambda r: compute_compatibility(me_row, r), reverse=True)

    if not rows:
        await interaction.response.send_message(
            f"Personne n'est dispo pour : {label}. "
            "Essaie `/dispo-maintenant` pour prévenir tout le monde quand toi tu es libre, ou réessaie plus tard.",
            ephemeral=True,
        )
        return

    lines = []
    for row in rows[:10]:
        user = await bot.fetch_user(int(row["user_id"]))
        name = user.display_name if hasattr(user, "display_name") else user.name
        compat = compute_compatibility(me_row, row)
        lines.append(f"🎾 **{name}** - Compatibilité : **{compat}%**")

    embed = mk_embed(f"Disponibles : {label}", "\n".join(lines), color=discord.Color.orange())
    embed.set_footer(text="La compatibilité combine niveau, secteur, objectif, disponibilités communes et nouveauté du partenaire.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------- QUESTIONNAIRE NIVEAU ----------

YEARS_CHOICES = [
    app_commands.Choice(name="Je n'ai jamais joué", value="jamais"),
    app_commands.Choice(name="Moins de 1 an", value="moins_1"),
    app_commands.Choice(name="1 à 3 ans", value="1_3"),
    app_commands.Choice(name="3 à 10 ans", value="3_10"),
    app_commands.Choice(name="Plus de 10 ans", value="plus_10"),
]

NTRP_CHOICES = [
    app_commands.Choice(name="Débutant complet", value="debutant"),
    app_commands.Choice(name="Je me situe à 2.5-3.0", value="2.5_3"),
    app_commands.Choice(name="Je me situe à 3.5", value="3.5"),
    app_commands.Choice(name="Je me situe à 4.0", value="4.0"),
    app_commands.Choice(name="4.5 et plus", value="4.5_plus"),
]

FREQ_CHOICES = [
    app_commands.Choice(name="Je ne joue jamais", value="jamais"),
    app_commands.Choice(name="Rarement (quelques fois par an)", value="rarement"),
    app_commands.Choice(name="Régulièrement (1-2x/semaine)", value="regulierement"),
    app_commands.Choice(name="Très souvent (3x/semaine ou plus)", value="tres_souvent"),
]


@bot.tree.command(name="questionnaire", description="Estime ton niveau pour information (n'affecte pas ton Elo).")
@app_commands.choices(annees=YEARS_CHOICES, niveau_ressenti=NTRP_CHOICES, frequence=FREQ_CHOICES)
async def questionnaire(
    interaction: discord.Interaction,
    annees: app_commands.Choice[str],
    niveau_ressenti: app_commands.Choice[str],
    frequence: app_commands.Choice[str],
):
    estimate = elo.estimate_starting_elo(annees.value, niveau_ressenti.value, frequence.value)
    await interaction.response.send_message(
        f"Estimation indicative de ton niveau : **{estimate}**.\n"
        "Tout le monde commence à **0 Elo** et grimpe uniquement en jouant des matchs confirmés - "
        "cette estimation ne change pas ton classement réel.",
        ephemeral=True,
    )


# ---------- RESULTATS DE MATCH ----------

def current_week_key() -> str:
    year, week, _ = datetime.now(TZ).isocalendar()
    return f"{year}-W{week:02d}"


REPUTATION_OPTIONS = [
    discord.SelectOption(label="Ponctuel", value="ponctuel"),
    discord.SelectOption(label="Agréable", value="agreable"),
    discord.SelectOption(label="Bon niveau", value="bon_niveau"),
    discord.SelectOption(label="Rejouerais avec lui", value="rejouerais"),
]


class RatingSelect(discord.ui.Select):
    def __init__(self, rated_id):
        super().__init__(
            placeholder="Choisis ce qui s'applique (optionnel)",
            options=REPUTATION_OPTIONS, min_values=0, max_values=len(REPUTATION_OPTIONS),
        )
        self.rated_id = rated_id

    async def callback(self, interaction: discord.Interaction):
        if self.values:
            db.add_reputation(self.rated_id, set(self.values))
            labels = [o.label for o in REPUTATION_OPTIONS if o.value in self.values]
            await interaction.response.send_message(f"Merci ! Tags envoyés : {', '.join(labels)}", ephemeral=True)
        else:
            await interaction.response.send_message("Pas de problème, aucun tag envoyé.", ephemeral=True)


class PresenceButton(discord.ui.Button):
    def __init__(self, rated_id, came: bool):
        super().__init__(
            label="Present ✅" if came else "Absent ❌",
            style=discord.ButtonStyle.success if came else discord.ButtonStyle.danger,
            row=1,
        )
        self.rated_id = rated_id
        self.came = came

    async def callback(self, interaction: discord.Interaction):
        db.add_presence(self.rated_id, self.came)
        for child in self.view.children:
            child.disabled = True
        await interaction.response.edit_message(content="Merci, c'est noté !", view=self.view)


class RatingView(discord.ui.View):
    def __init__(self, rated_id):
        super().__init__(timeout=600)
        self.add_item(RatingSelect(rated_id))
        self.add_item(PresenceButton(rated_id, True))
        self.add_item(PresenceButton(rated_id, False))


class ConfirmMatchView(discord.ui.View):
    def __init__(self, match_id: int, opponent_id: int):
        super().__init__(timeout=86400)
        self.match_id = match_id
        self.opponent_id = opponent_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message(
                "Seul l'adversaire concerné peut confirmer ou contester ce match.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirmer", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        match = db.get_pending_match(self.match_id)
        if not match or match["status"] != "pending":
            await interaction.response.send_message("Ce match n'est plus en attente.", ephemeral=True)
            return

        p1, p2 = match["player1_id"], match["player2_id"]
        winner = match["winner_id"]
        loser = p2 if winner == p1 else p1

        db.ensure_player(p1)
        db.ensure_player(p2)
        winner_row = db.get_player(winner)
        loser_row = db.get_player(loser)

        is_new_partner = not db.has_played_before(p1, p2)

        new_winner_elo, new_loser_elo = elo.update_elo(winner_row["elo"], loser_row["elo"])
        db.set_elo(winner, new_winner_elo)
        db.set_elo(loser, new_loser_elo)
        await sync_tier_role(interaction.guild, winner, new_winner_elo)
        await sync_tier_role(interaction.guild, loser, new_loser_elo)
        db.record_match_result(
            p1, p2, winner, match["score"],
            winner_row["elo"] if winner == p1 else loser_row["elo"],
            winner_row["elo"] if winner == p2 else loser_row["elo"],
            new_winner_elo if winner == p1 else new_loser_elo,
            new_winner_elo if winner == p2 else new_loser_elo,
        )
        db.set_match_status(self.match_id, "confirmed")

        challenge_note = ""
        if is_new_partner:
            week_key = current_week_key()
            db.mark_weekly_challenge(p1, week_key)
            db.mark_weekly_challenge(p2, week_key)
            challenge_note = "\n\nDéfi de la semaine complété pour les deux joueurs : **nouveau partenaire** !"

        embed = mk_embed(
            "Match confirmé !",
            f"Score : {match['score']}\n<@{winner}> remporte le match.\n\n"
            f"Nouveau Elo gagnant : **{round(new_winner_elo)}**\n"
            f"Nouveau Elo perdant : **{round(new_loser_elo)}**"
            f"{challenge_note}",
            color=discord.Color.orange(),
        )
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

        rated_id = p1 if interaction.user.id == int(p2) else p2
        await interaction.followup.send(
            f"Comment s'est passé le match avec <@{rated_id}> ? Et est-ce que <@{rated_id}> s'est présenté ? (optionnel)",
            view=RatingView(rated_id),
            ephemeral=True,
        )

    @discord.ui.button(label="Contester", style=discord.ButtonStyle.danger)
    async def contest(self, interaction: discord.Interaction, button: discord.ui.Button):
        db.set_match_status(self.match_id, "cancelled")
        embed = mk_embed("Match annulé", "Les deux joueurs ne sont pas d'accord. Ce match ne compte pas dans le classement.", color=discord.Color.orange())
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)


@bot.tree.command(name="resultat", description="Enregistre le résultat d'un match (l'adversaire doit confirmer).")
@app_commands.describe(adversaire="Ton adversaire", vainqueur="Qui a gagné le match", score="Le score (ex: 6-4 6-3)")
async def resultat(interaction: discord.Interaction, adversaire: discord.Member, vainqueur: discord.Member, score: str):
    if adversaire.id == interaction.user.id:
        await interaction.response.send_message("Tu ne peux pas jouer contre toi-même.", ephemeral=True)
        return
    if vainqueur.id not in (interaction.user.id, adversaire.id):
        await interaction.response.send_message("Le vainqueur doit être toi ou ton adversaire.", ephemeral=True)
        return

    db.ensure_player(interaction.user.id)
    db.ensure_player(adversaire.id)

    match_id = db.create_pending_match(interaction.user.id, adversaire.id, vainqueur.id, score)

    embed = mk_embed(
        "Résultat de match en attente de confirmation",
        f"<@{interaction.user.id}> déclare : **<@{vainqueur.id}> gagne {score}** contre <@{adversaire.id if vainqueur.id == interaction.user.id else interaction.user.id}>.\n\n"
        f"<@{adversaire.id}>, confirme ou conteste ce résultat ci-dessous.",
        color=discord.Color.orange(),
    )
    view = ConfirmMatchView(match_id, adversaire.id)
    await interaction.response.send_message(content=f"<@{adversaire.id}>", embed=embed, view=view)


# ---------- CLASSEMENT, BADGES, DEFI, STATS ----------

CATEGORIE_CHOICES = [
    app_commands.Choice(name="Elo", value="elo"),
    app_commands.Choice(name="Plus actif (matchs joués)", value="actif"),
    app_commands.Choice(name="Plus de partenaires différents", value="partenaires"),
    app_commands.Choice(name="Plus apprécié (réputation)", value="apprecie"),
    app_commands.Choice(name="Plus de filleuls parrainés", value="parrainage"),
]


@bot.tree.command(name="classement", description="Affiche le classement du club (Elo, activité, partenaires, réputation).")
@app_commands.describe(categorie="Quel classement veux-tu voir ?")
@app_commands.choices(categorie=CATEGORIE_CHOICES)
async def classement(interaction: discord.Interaction, categorie: app_commands.Choice[str] = None):
    cat = categorie.value if categorie else "elo"
    lines = []

    if cat == "elo":
        rows = db.get_leaderboard(15)
        for i, row in enumerate(rows, start=1):
            user = await bot.fetch_user(int(row["user_id"]))
            lines.append(f"**{i}.** {user.display_name if hasattr(user, 'display_name') else user.name} - {round(row['elo'])} Elo ({row['wins']}V / {row['losses']}D)")
        title = "Classement Elo du club"

    elif cat == "actif":
        rows = db.get_leaderboard_by_matches(15)
        for i, row in enumerate(rows, start=1):
            user = await bot.fetch_user(int(row["user_id"]))
            lines.append(f"**{i}.** {user.display_name if hasattr(user, 'display_name') else user.name} - {row['matches_played']} matchs joués")
        title = "Classement des plus actifs"

    elif cat == "partenaires":
        rows = db.get_all_active_players()
        counts = [(row, len(db.get_distinct_partners(row["user_id"]))) for row in rows]
        counts.sort(key=lambda c: c[1], reverse=True)
        for i, (row, count) in enumerate(counts[:15], start=1):
            user = await bot.fetch_user(int(row["user_id"]))
            lines.append(f"**{i}.** {user.display_name if hasattr(user, 'display_name') else user.name} - {count} partenaire(s) différent(s)")
        title = "Classement par diversité de partenaires"

    elif cat == "parrainage":
        rows = db.get_leaderboard_by_referrals(15)
        for i, row in enumerate(rows, start=1):
            user = await bot.fetch_user(int(row["user_id"]))
            lines.append(f"**{i}.** {user.display_name if hasattr(user, 'display_name') else user.name} - {row['c']} filleul(s)")
        title = "Classement des ambassadeurs (parrainage)"

    else:  # apprecie
        rows = db.get_leaderboard_by_reputation(15)
        for i, row in enumerate(rows, start=1):
            user = await bot.fetch_user(int(row["user_id"]))
            ratio = round(100 * row["rejouerais"] / row["total_ratings"])
            lines.append(f"**{i}.** {user.display_name if hasattr(user, 'display_name') else user.name} - {ratio}% rejoueraient avec lui ({row['total_ratings']} avis)")
        title = "Classement par réputation"

    if not lines:
        await interaction.response.send_message("Pas encore assez de données pour ce classement.")
        return

    embed = mk_embed(title, "\n".join(lines), color=discord.Color.orange())
    await interaction.response.send_message(embed=embed)


def compute_week_streak(user_id) -> int:
    dates = db.get_match_dates(user_id)
    if not dates:
        return 0
    weeks = set()
    for d in dates:
        dt = datetime.strptime(d, "%Y-%m-%d")
        weeks.add(dt.isocalendar()[:2])
    year, week = datetime.now(TZ).isocalendar()[:2]
    streak = 0
    while (year, week) in weeks:
        streak += 1
        monday = datetime.strptime(f"{year}-W{week:02d}-1", "%G-W%V-%u") - timedelta(days=7)
        year, week = monday.isocalendar()[:2]
    return streak


def compute_badges(user_id) -> list:
    badges = []
    p = db.get_player(user_id)
    if not p:
        return badges

    if p["matches_played"] >= 1:
        badges.append("🎾 Premier match")
    if p["matches_played"] >= 10:
        badges.append("10 matchs")
    if p["matches_played"] >= 50:
        badges.append("50 matchs")
    if p["wins"] >= 10:
        badges.append("🏆 10 victoires")

    partners = db.get_distinct_partners(user_id)
    if len(partners) >= 20:
        badges.append("🌐 20 partenaires différents")
    if len(partners) >= 10 or db.get_initiated_count(user_id) >= 10:
        badges.append("🤝 Joueur social")

    streak = compute_week_streak(user_id)
    if streak >= 2:
        badges.append(f"Série de {streak} semaines")
    if streak >= 5:
        badges.append("🔥 5 semaines consécutives")

    if db.get_initiated_count(user_id) >= 5:
        badges.append("Organisateur")

    rep = db.get_reputation(user_id)
    if rep and rep["total_ratings"] >= 5 and (rep["rejouerais"] / rep["total_ratings"]) >= 0.8:
        badges.append("Joueur fiable")

    if db.get_referral_count(user_id) >= 3:
        badges.append("📣 Ambassadeur")

    return badges


@bot.tree.command(name="badges", description="Affiche les badges d'un membre.")
async def badges(interaction: discord.Interaction, membre: discord.Member = None):
    target = membre or interaction.user
    earned = compute_badges(target.id)
    if not earned:
        await interaction.response.send_message(
            f"{target.display_name} n'a pas encore de badge. Joue des matchs et reste actif pour en débloquer !",
        )
        return
    embed = mk_embed(f"Badges de {target.display_name}", "\n".join(f"- {b}" for b in earned), color=discord.Color.orange())
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="stats", description="Affiche les statistiques d'un membre.")
async def stats(interaction: discord.Interaction, membre: discord.Member = None):
    target = membre or interaction.user
    db.ensure_player(target.id)
    p = db.get_player(target.id)
    partners = db.get_distinct_partners(target.id)
    win_rate = round(100 * p["wins"] / p["matches_played"]) if p["matches_played"] > 0 else 0

    embed = mk_embed(f"Statistiques de {target.display_name}", color=discord.Color.orange())
    embed.add_field(name="Parties jouées", value=str(p["matches_played"]), inline=True)
    embed.add_field(name="Taux de victoire", value=f"{win_rate}%", inline=True)
    embed.add_field(name="Partenaires différents", value=str(len(partners)), inline=True)
    embed.add_field(name="Série de semaines actives", value=str(compute_week_streak(target.id)), inline=True)

    fav_id, fav_count = db.get_favorite_partner(target.id)
    if fav_id:
        fav_user = await bot.fetch_user(int(fav_id))
        fav_name = fav_user.display_name if hasattr(fav_user, "display_name") else fav_user.name
        embed.add_field(name="Partenaire favori", value=f"{fav_name} ({fav_count} matchs)", inline=True)

    embed.add_field(name="Tie-breaks joués (estimé)", value=str(db.get_tiebreak_count(target.id)), inline=True)

    last_match = db.get_last_match_date(target.id)
    if last_match:
        days_ago = (datetime.now(TZ) - datetime.fromisoformat(last_match).replace(tzinfo=TZ)).days
        embed.add_field(name="Dernier match", value=f"il y a {days_ago} jour(s)" if days_ago > 0 else "aujourd'hui", inline=True)

    embed.add_field(name="Filleuls parrainés", value=str(db.get_referral_count(target.id)), inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="objectifs", description="Tes objectifs personnels et ta progression.")
async def objectifs(interaction: discord.Interaction):
    target = interaction.user
    db.ensure_player(target.id)
    p = db.get_player(target.id)
    partners = db.get_distinct_partners(target.id)
    streak = compute_week_streak(target.id)

    lines = []
    lines.append(f"{'✅' if streak >= 1 else '⬜'} Jouer au moins 1x cette semaine ({'fait' if streak >= 1 else 'pas encore'})")
    lines.append(f"{'✅' if compute_tier(p['elo']) == 'Expert' else '⬜'} Atteindre le niveau Expert (actuellement {compute_tier(p['elo'])}, {round(p['elo'])} Elo)")
    lines.append(f"{'✅' if len(partners) >= 10 else '⬜'} Rencontrer 10 partenaires différents ({len(partners)}/10)")

    embed = mk_embed(f"Objectifs personnels de {target.display_name}", "\n".join(lines), color=discord.Color.orange())
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="parrainage", description="Indique qui t'a invité à rejoindre le club (récompense ton parrain).")
@app_commands.describe(parrain="Le membre qui t'a invité/parrainé")
async def parrainage(interaction: discord.Interaction, parrain: discord.Member):
    if parrain.id == interaction.user.id:
        await interaction.response.send_message("Tu ne peux pas te parrainer toi-même.", ephemeral=True)
        return
    p = db.get_player(interaction.user.id)
    if p and p["referred_by"]:
        await interaction.response.send_message("Tu as déjà indiqué un parrain.", ephemeral=True)
        return
    db.set_referred_by(interaction.user.id, parrain.id)
    db.add_xp(parrain.id, 100)
    await interaction.response.send_message(
        f"Merci ! <@{parrain.id}> a été crédité de **+100 XP** pour t'avoir parrainé. 🎾", ephemeral=False,
    )


@bot.tree.command(name="defi", description="Affiche le défi de la semaine et si tu l'as complété.")
async def defi(interaction: discord.Interaction):
    week_key = current_week_key()
    done = db.has_weekly_challenge(interaction.user.id, week_key)
    status = "Complété !" if done else "Pas encore complété."
    embed = mk_embed(
        "Défi de la semaine",
        "**Joue un match avec un nouveau partenaire** (quelqu'un que tu n'as jamais affronté).\n\n"
        f"Statut : {status}\n\n"
        "Utilise `/trouver-partenaire` pour trouver quelqu'un de nouveau !",
        color=discord.Color.orange(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


def build_elo_info_embed() -> discord.Embed:
    embed = mk_embed("Comment fonctionne l'Elo ?", color=discord.Color.orange())
    embed.add_field(
        name="Niveau de départ",
        value=(
            "Tout le monde commence à **0 Elo**, peu importe son niveau de tennis. "
            "Il faut jouer des matchs pour grimper - `/questionnaire` donne juste une estimation indicative, "
            "ça ne change pas ton vrai classement. "
            "Seul un Founder/Modérateur peut forcer ton Elo manuellement via `/modifier-profil` (cas exceptionnel)."
        ),
        inline=False,
    )
    embed.add_field(
        name="Comment l'Elo bouge après un match",
        value=(
            "À chaque match confirmé, le système regarde l'écart entre les deux joueurs **avant** de calculer le gain/la perte :\n"
            "- Si un joueur **moins fort gagne contre un joueur plus fort**, il gagne **beaucoup** de points (jusqu'à ~35).\n"
            "- Si le **favori gagne comme attendu**, il ne gagne presque rien (parfois moins de 1 point).\n"
            "- Le perdant perd exactement ce que le gagnant a gagné (l'Elo total du club reste stable)."
        ),
        inline=False,
    )
    embed.add_field(
        name="Exemples concrets",
        value=(
            "**200 bat 1000** -> le 200 monte à ~235, le 1000 descend à ~965 (gros bond !)\n"
            "**1000 bat 200** -> le 1000 monte à ~1000.4, le 200 descend à ~199.6 (presque rien)\n"
            "**600 bat 600** (match équilibré) -> +18 / -18 environ"
        ),
        inline=False,
    )
    embed.add_field(
        name="Pourquoi ce système ?",
        value=(
            "Le but est de **t'encourager à jouer contre des gens plus forts que toi** : "
            "tu n'as presque rien à perdre et beaucoup à gagner. À l'inverse, écraser des débutants "
            "ne fait presque pas progresser ton classement. Plus tu joues, plus ton Elo reflétera ton vrai niveau."
        ),
        inline=False,
    )
    embed.add_field(
        name="Voir ta progression",
        value="`/profil` pour ton Elo actuel - `/historique` pour tes derniers matchs - `/classement` pour le top du club.",
        inline=False,
    )
    return embed


ELO_INFO_CHANNEL_NAME = "comment-marche-lelo"


async def get_or_create_elo_info_channel(guild: discord.Guild) -> discord.TextChannel:
    channel = discord.utils.find(lambda c: c.name == ELO_INFO_CHANNEL_NAME, guild.text_channels)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    for role in get_mod_roles(guild):
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    if not channel:
        channel = await guild.create_text_channel(
            ELO_INFO_CHANNEL_NAME, overwrites=overwrites, topic="Explication du système de classement Elo du club."
        )
    else:
        for target, overwrite in overwrites.items():
            await channel.set_permissions(target, overwrite=overwrite)
    return channel


async def ensure_elo_info_message(guild: discord.Guild):
    channel = await get_or_create_elo_info_channel(guild)
    history = [m async for m in channel.history(limit=1)]
    if not history:
        await channel.send(embed=build_elo_info_embed())


# ---------- HISTORIQUE ----------

@bot.tree.command(name="historique", description="Affiche tes derniers matchs confirmés.")
async def historique(interaction: discord.Interaction, membre: discord.Member = None):
    target = membre or interaction.user
    rows = db.get_match_history(target.id, limit=5)
    if not rows:
        await interaction.response.send_message(f"{target.display_name} n'a pas encore de match confirmé.")
        return

    lines = []
    for row in rows:
        winner_mention = f"<@{row['winner_id']}>"
        lines.append(f"{row['created_at'][:10]} - Score {row['score']} - Vainqueur : {winner_mention}")

    embed = mk_embed(f"Historique de {target.display_name}", "\n".join(lines))
    await interaction.response.send_message(embed=embed)


# ---------- CARTE DES TERRAINS ----------

TERRAINS_CHANNEL_NAME = "carte-des-terrains"


async def get_or_create_terrains_channel(guild: discord.Guild) -> discord.TextChannel:
    channel = discord.utils.find(lambda c: c.name == TERRAINS_CHANNEL_NAME, guild.text_channels)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    for role in get_mod_roles(guild):
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    if not channel:
        channel = await guild.create_text_channel(
            TERRAINS_CHANNEL_NAME, overwrites=overwrites,
            topic="Liste des terrains de tennis du coin + qui y est présentement.",
        )
    else:
        for target, overwrite in overwrites.items():
            await channel.set_permissions(target, overwrite=overwrite)
    return channel


class AjouterTerrainModal(discord.ui.Modal, title="Ajouter un terrain"):
    nom = discord.ui.TextInput(label="Nom du terrain / parc", max_length=80, required=True)
    nombre = discord.ui.TextInput(label="Nombre de terrains", max_length=3, required=True)
    eclairage = discord.ui.TextInput(label="Éclairé le soir ? (Oui/Non)", max_length=3, required=True)
    gratuit = discord.ui.TextInput(label="Gratuit ? (Oui/Non)", max_length=3, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            count = int(str(self.nombre.value).strip())
        except ValueError:
            await interaction.response.send_message("Le nombre de terrains doit être un nombre.", ephemeral=True)
            return
        db.add_court(str(self.nom.value).strip(), count, str(self.eclairage.value).strip(), str(self.gratuit.value).strip())
        await interaction.response.send_message(f"Terrain **{self.nom.value}** ajouté ! Utilise `/terrains` pour le voir.", ephemeral=True)


@bot.tree.command(name="ajouter-terrain", description="(Founder/Mod) Ajoute un terrain à la carte des terrains.")
async def ajouter_terrain(interaction: discord.Interaction):
    if not is_mod(interaction.user):
        await interaction.response.send_message("Seuls les Founder/Modérateur peuvent ajouter un terrain.", ephemeral=True)
        return
    await interaction.response.send_modal(AjouterTerrainModal())


class CourtSelect(discord.ui.Select):
    def __init__(self, courts):
        options = [discord.SelectOption(label=c["name"][:100], value=str(c["id"])) for c in courts][:25]
        super().__init__(placeholder="\U0001F449 Choisis le terrain où tu es", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        self.view.selected_court_id = int(self.values[0])
        await interaction.response.edit_message(content=self.view.text(), view=self.view)


class JeSuisIciButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="📍 Je suis ici (2h)", style=discord.ButtonStyle.success, row=1)

    async def callback(self, interaction: discord.Interaction):
        view: TerrainsView = self.view
        if view.selected_court_id is None:
            await interaction.response.send_message("Choisis d'abord un terrain dans le menu.", ephemeral=True)
            return
        until = datetime.now(TZ) + timedelta(hours=2)
        db.set_court_presence(interaction.user.id, view.selected_court_id, until.isoformat())
        court = db.get_court(view.selected_court_id)
        await interaction.response.send_message(
            f"📍 {interaction.user.mention} est au terrain **{court['name']}** pour les 2 prochaines heures !"
        )


class TerrainsView(discord.ui.View):
    def __init__(self, courts):
        super().__init__(timeout=900)
        self.courts = courts
        self.selected_court_id = None
        if courts:
            self.add_item(CourtSelect(courts))
        self.add_item(JeSuisIciButton())

    def text(self) -> str:
        if not self.courts:
            return "Aucun terrain enregistré pour le moment. Un Founder/Mod peut en ajouter avec `/ajouter-terrain`."
        lines = ["\U0001F3DE **Terrains disponibles**", ""]
        for c in self.courts:
            lines.append(f"- **{c['name']}** - {c['count']} terrain(s) - Éclairé: {c['lighting']} - Gratuit: {c['free']}")
        now_iso = datetime.now(TZ).isoformat()
        present = db.get_active_court_presence(now_iso)
        if present:
            lines.append("")
            lines.append("**Présentement sur place :**")
            for p in present:
                lines.append(f"- <@{p['user_id']}> @ {p['court_name']}")
        lines.append("")
        lines.append("Choisis un terrain ci-dessous puis clique **Je suis ici** pour annoncer ta présence.")
        return "\n".join(lines)


@bot.tree.command(name="terrains", description="Affiche la carte des terrains et qui y est présentement.")
async def terrains(interaction: discord.Interaction):
    courts = list(db.get_courts())
    view = TerrainsView(courts)
    await interaction.response.send_message(view.text(), view=view)


# ---------- RECOMPENSES MENSUELLES AUTOMATIQUES ----------

RECOMPENSES_CHANNEL_NAME = "recompenses"


async def get_or_create_recompenses_channel(guild: discord.Guild) -> discord.TextChannel:
    channel = discord.utils.find(lambda c: c.name == RECOMPENSES_CHANNEL_NAME, guild.text_channels)
    if channel:
        return channel
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    for role in get_mod_roles(guild):
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    return await guild.create_text_channel(
        RECOMPENSES_CHANNEL_NAME, overwrites=overwrites,
        topic="Récompenses et palmarès automatiques du mois.",
    )


async def send_monthly_rewards():
    if not GUILD_ID:
        return
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    last_month = (datetime.now(TZ).replace(day=1) - timedelta(days=1))
    year_month = last_month.strftime("%Y-%m")

    match_counts = db.get_monthly_match_counts(year_month)
    new_partner_counts = db.get_monthly_new_partner_counts(year_month)

    lines = [f"🏆 **Palmares du mois de {year_month}**", ""]

    if match_counts:
        top_active = max(match_counts, key=match_counts.get)
        user = await bot.fetch_user(int(top_active))
        lines.append(f"🔥 **Plus actif** : {user.display_name if hasattr(user, 'display_name') else user.name} ({match_counts[top_active]} matchs)")

    if new_partner_counts:
        top_social = max(new_partner_counts, key=new_partner_counts.get)
        user = await bot.fetch_user(int(top_social))
        lines.append(f"🤝 **Plus de nouveaux partenaires** : {user.display_name if hasattr(user, 'display_name') else user.name} ({new_partner_counts[top_social]})")

    best_streak, best_streak_uid = 0, None
    for p in db.get_all_active_players():
        s = compute_week_streak(p["user_id"])
        if s > best_streak:
            best_streak, best_streak_uid = s, p["user_id"]
    if best_streak_uid:
        user = await bot.fetch_user(int(best_streak_uid))
        lines.append(f"📅 **Plus longue serie active** : {user.display_name if hasattr(user, 'display_name') else user.name} ({best_streak} semaines)")

    rep_rows = db.get_leaderboard_by_reputation(1)
    if rep_rows:
        top_liked = rep_rows[0]
        user = await bot.fetch_user(int(top_liked["user_id"]))
        ratio = round(100 * top_liked["rejouerais"] / top_liked["total_ratings"])
        lines.append(f"⭐ **Plus apprecie** : {user.display_name if hasattr(user, 'display_name') else user.name} ({ratio}% rejoueraient avec lui)")

    if len(lines) <= 2:
        return  # rien a annoncer ce mois-ci

    try:
        channel = await get_or_create_recompenses_channel(guild)
        await channel.send("\n".join(lines))
    except Exception as e:
        print(f"[monthly_rewards] ERREUR: {e!r}", flush=True)


@tasks.loop(hours=24)
async def monthly_rewards_loop():
    now = datetime.now(TZ)
    if now.day != 1:
        return
    last_run = db.get_kv("monthly_rewards_last_run")
    this_key = now.strftime("%Y-%m")
    if last_run == this_key:
        return
    await send_monthly_rewards()
    db.set_kv("monthly_rewards_last_run", this_key)


# ---------- NOTIFICATIONS INTELLIGENTES ----------

async def send_smart_notifications():
    if not GUILD_ID:
        return
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    today_str = datetime.now(TZ).date().isoformat()
    if db.get_kv("smart_notif_last_run") == today_str:
        return

    day_index, hour = current_day_and_hour()
    rows = list(db.search_recurring(day_index, hour=None, secteur=None))
    rows += list(db.search_instant(datetime.now(TZ).isoformat()))

    by_tier = {"Debutant": set(), "Intermediaire": set(), "Expert": set()}
    for r in rows:
        by_tier[compute_tier(r["elo"])].add(r["user_id"])

    tier_roles = await get_or_create_tier_roles(guild)
    for tier_name, ids in by_tier.items():
        if len(ids) < 2:
            continue
        channel_name = f"partenaire-{tier_name.lower()}"
        channel = discord.utils.find(lambda c, n=channel_name: c.name == n, guild.text_channels)
        if channel:
            try:
                await channel.send(
                    f"🔔 **{len(ids)} joueurs {tier_name}** sont disponibles ce soir ! "
                    "Utilise `/trouver-partenaire` (Maintenant) pour les trouver."
                )
            except Exception as e:
                print(f"[smart_notif] ERREUR sur {channel_name}: {e!r}", flush=True)

    db.set_kv("smart_notif_last_run", today_str)


@tasks.loop(minutes=60)
async def smart_notifications_loop():
    now = datetime.now(TZ)
    if now.hour == 19:
        await send_smart_notifications()


# ---------- EVENEMENT AUTOMATIQUE DU VENDREDI ----------

class EventJoinButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"event_join:(?P<event_key>.+)",
):
    def __init__(self, event_key: str):
        super().__init__(
            discord.ui.Button(
                label="🙋 Je veux jouer !",
                style=discord.ButtonStyle.success,
                custom_id=f"event_join:{event_key}",
            )
        )
        self.event_key = event_key

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["event_key"])

    async def callback(self, interaction: discord.Interaction):
        participants = set(json.loads(db.get_kv(self.event_key, "[]")))
        participants.add(str(interaction.user.id))
        db.set_kv(self.event_key, json.dumps(list(participants)))
        await interaction.response.send_message(
            f"Tu es inscrit ! Pour l'instant {len(participants)} joueur(s) intéressé(s). "
            "On vous regroupera par paires bientôt.", ephemeral=True,
        )


class EventJoinView(discord.ui.View):
    def __init__(self, event_key: str):
        super().__init__(timeout=None)
        self.add_item(EventJoinButton(event_key))


async def send_friday_event(guild: discord.Guild):
    week_key = current_week_key()
    event_key = f"event_{week_key}"
    channel = await get_or_create_recherche_channel(guild)
    embed = mk_embed(
        "🎾 Qui veut jouer samedi matin ?",
        "Clique sur le bouton ci-dessous si tu es intéressé à jouer **samedi matin**. "
        "On va regrouper les joueurs intéressés en paires dimanche matin !",
        color=discord.Color.orange(),
    )
    await channel.send(embed=embed, view=EventJoinView(event_key))


async def send_friday_event_groups(guild: discord.Guild):
    week_key = current_week_key()
    event_key = f"event_{week_key}"
    participants = json.loads(db.get_kv(event_key, "[]"))
    if len(participants) < 2:
        return
    channel = await get_or_create_recherche_channel(guild)
    lines = ["🎾 **Voici les groupes pour samedi matin !**", ""]
    for i in range(0, len(participants) - 1, 2):
        try:
            match_channel = await get_or_create_match_channel(guild, participants[i], participants[i + 1])
            lines.append(f"- <@{participants[i]}> contre <@{participants[i+1]}> -> {match_channel.mention}")
        except Exception as e:
            print(f"[friday_event_groups] salon prive impossible: {e!r}", flush=True)
            lines.append(f"- <@{participants[i]}> contre <@{participants[i+1]}>")
    if len(participants) % 2 == 1:
        lines.append(f"- <@{participants[-1]}> : tu peux te joindre a un des groupes ci-dessus !")
    await channel.send("\n".join(lines))


@tasks.loop(hours=24)
async def friday_event_loop():
    if not GUILD_ID:
        return
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    now = datetime.now(TZ)
    today_str = now.date().isoformat()

    if now.weekday() == 4 and db.get_kv("friday_event_last_run") != today_str:  # Vendredi
        try:
            await send_friday_event(guild)
        except Exception as e:
            print(f"[friday_event] ERREUR: {e!r}", flush=True)
        db.set_kv("friday_event_last_run", today_str)

    if now.weekday() == 6 and db.get_kv("friday_event_groups_last_run") != today_str:  # Dimanche
        try:
            await send_friday_event_groups(guild)
        except Exception as e:
            print(f"[friday_event_groups] ERREUR: {e!r}", flush=True)
        db.set_kv("friday_event_groups_last_run", today_str)


# ---------- PANNEAU D'ACTIONS RAPIDES (dans chaque espace personnel) ----------

class EspaceDisponibiliteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="📅 Disponibilites", style=discord.ButtonStyle.primary, row=0, custom_id="espace_dispo")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(DisponibiliteModal(interaction.user.id))


class EspaceTrouverPartenaireButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🎾 Trouver un partenaire", style=discord.ButtonStyle.primary, row=0, custom_id="espace_trouver_partenaire")

    async def callback(self, interaction: discord.Interaction):
        db.ensure_player(interaction.user.id)
        me_row = db.get_player(interaction.user.id)
        day_index, hour = current_day_and_hour()
        recurring = db.search_recurring(day_index, hour, exclude_user_id=interaction.user.id)
        instant = db.search_instant(datetime.now(TZ).isoformat(), exclude_user_id=interaction.user.id)
        seen, rows = set(), []
        for row in list(instant) + list(recurring):
            if row["user_id"] not in seen:
                seen.add(row["user_id"])
                rows.append(row)
        rows.sort(key=lambda r: compute_compatibility(me_row, r), reverse=True)

        if not rows:
            await interaction.response.send_message(
                "Personne n'est dispo pour jouer tout de suite. Utilise `/dispo-maintenant` pour prevenir les autres "
                "quand toi tu es libre !", ephemeral=True,
            )
            return

        lines = []
        for row in rows[:5]:
            user = await bot.fetch_user(int(row["user_id"]))
            name = user.display_name if hasattr(user, "display_name") else user.name
            lines.append(f"🎾 **{name}** - Compatibilite : {compute_compatibility(me_row, row)}%")

        await interaction.response.send_message(
            "**Voici qui pourrait jouer avec toi maintenant :**\n" + "\n".join(lines), ephemeral=True,
        )


class EspaceTerrainsButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="📍 Terrains", style=discord.ButtonStyle.secondary, row=1, custom_id="espace_terrains")

    async def callback(self, interaction: discord.Interaction):
        courts = list(db.get_courts())
        view = TerrainsView(courts)
        await interaction.response.send_message(view.text(), view=view, ephemeral=True)


class EspaceProfilButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="👤 Mon profil", style=discord.ButtonStyle.secondary, row=1, custom_id="espace_profil")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=build_profile_embed(interaction.user), ephemeral=True)


class EspaceModifierProfilButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="✏️ Modifier mon profil", style=discord.ButtonStyle.secondary, row=1, custom_id="espace_modifier_profil")

    async def callback(self, interaction: discord.Interaction):
        db.ensure_player(interaction.user.id)
        await interaction.response.send_modal(ModifierProfilModal(interaction.user.id, actor_is_mod=is_mod(interaction.user)))


class EspaceActionsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(EspaceDisponibiliteButton())
        self.add_item(EspaceTrouverPartenaireButton())
        self.add_item(EspaceTerrainsButton())
        self.add_item(EspaceProfilButton())
        self.add_item(EspaceModifierProfilButton())


ESPACE_PANEL_TEXT = (
    "**🎾 Tableau de bord rapide**\n"
    "Clique sur un bouton pour l'action correspondante :\n\n"
    "📅 **Disponibilités** - ouvre le formulaire pour dire quels jours et à quelles heures tu peux jouer.\n"
    "🎾 **Trouver un partenaire** - montre tout de suite qui est disponible pour jouer avec toi.\n"
    "📍 **Terrains** - affiche la carte des terrains et qui y est présentement.\n"
    "👤 **Mon profil** - affiche ton niveau, ton Elo, tes stats et tes badges.\n"
    "✏️ **Modifier mon profil** - ouvre le formulaire pour changer ton niveau, objectif, préférence, secteur ou âge."
)


async def ensure_espace_action_panel(channel: discord.TextChannel, user_id: int):
    """Envoie une fois par membre le tableau de bord, et l'épingle pour qu'il reste facile à retrouver."""
    flag_key = f"espace_panel_v3_sent_{user_id}"
    if db.get_kv(flag_key):
        return
    try:
        message = await channel.send(ESPACE_PANEL_TEXT, view=EspaceActionsView())
        try:
            await message.pin()
        except discord.HTTPException as e:
            print(f"[espace_panel] impossible d'epingler pour {user_id}: {e!r}", flush=True)
        db.set_kv(flag_key, "1")
    except Exception as e:
        print(f"[espace_panel] ERREUR pour {user_id}: {e!r}", flush=True)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("BOT_TOKEN manquant dans .env")
    bot.run(TOKEN)
