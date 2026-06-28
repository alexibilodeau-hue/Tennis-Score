import os
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import db
import elo

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

ESPACE_PERSONNEL_CATEGORY_NAME = "ESPACE PERSONNEL"
DEMANDE_ACCES_CATEGORY_NAME = "DEMANDES D'ACCES"
MOD_ROLE_NAMES = {"Founder", "Modérateur"}
MEMBRE_ROLE_NAME = "Membre"


def mk_embed(title, description="", color=discord.Color.green()):
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
        color=discord.Color.blurple(),
    )
    await channel.send(embed=embed)
    return channel


def get_mod_roles(guild: discord.Guild):
    return [r for r in guild.roles if r.name in MOD_ROLE_NAMES]


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
    embed = mk_embed(
        f"Bienvenue {member.display_name} !",
        "Presente-toi ici (prenom, niveau au tennis, depuis quand tu joues, pourquoi tu veux rejoindre).\n\n"
        "Seuls toi et l'equipe (Founder / Moderateur) peuvent voir ce salon. "
        "Une fois ta demande validee, tu auras acces a tout le serveur.",
        color=discord.Color.orange(),
    )
    await channel.send(embed=embed)
    return channel


async def announce_new_member(member: discord.Member):
    channel = discord.utils.find(lambda c: c.name == "bienvenue", member.guild.text_channels)
    if not channel:
        return
    embed = mk_embed(
        "Nouveau membre !",
        f"Bienvenue {member.mention} sur le serveur Tennis Sherbrooke ! 🎾\n\n"
        "Va te presenter dans ton salon de demande d'acces prive pour obtenir ton acces complet.",
        color=discord.Color.green(),
    )
    await channel.send(embed=embed)


@bot.event
async def on_member_remove(member: discord.Member):
    if member.bot:
        return
    if GUILD_ID and member.guild.id != GUILD_ID:
        return
    role_ids = [r.id for r in member.roles if r.name != "@everyone"]
    if role_ids:
        db.save_roles(member.id, role_ids)


@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return
    if GUILD_ID and member.guild.id != GUILD_ID:
        return

    saved_role_ids = db.get_saved_roles(member.id)
    if saved_role_ids:
        roles_to_restore = [member.guild.get_role(rid) for rid in saved_role_ids]
        roles_to_restore = [r for r in roles_to_restore if r is not None]
        if roles_to_restore:
            await member.add_roles(*roles_to_restore, reason="Restauration des roles apres re-entree sur le serveur")

    await get_or_create_personal_channel(member)
    await get_or_create_demande_channel(member)
    await announce_new_member(member)


@bot.event
async def on_ready():
    db.init_db()
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
            async for member in guild.fetch_members(limit=None):
                if not member.bot:
                    await get_or_create_personal_channel(member)
                    await get_or_create_demande_channel(member)
            print("Espaces personnels et demandes d'acces verifies pour tous les membres actuels.")


# ---------- PROFIL ----------

OBJECTIF_CHOICES = [
    app_commands.Choice(name="Loisir", value="loisir"),
    app_commands.Choice(name="Progression", value="progression"),
    app_commands.Choice(name="Competition", value="competition"),
]

PREFERENCE_CHOICES = [
    app_commands.Choice(name="Simple", value="simple"),
    app_commands.Choice(name="Double", value="double"),
    app_commands.Choice(name="Les deux", value="les_deux"),
]


@bot.tree.command(name="mon-espace", description="Cree (ou retrouve) ton espace personnel prive sur le serveur.")
async def mon_espace(interaction: discord.Interaction):
    channel = await get_or_create_personal_channel(interaction.user)
    await interaction.response.send_message(f"Ton espace personnel : {channel.mention}", ephemeral=True)


@bot.tree.command(name="profil", description="Affiche ton profil joueur (ou celui d'un autre membre).")
@app_commands.describe(membre="Le membre dont tu veux voir le profil (optionnel)")
async def profil(interaction: discord.Interaction, membre: discord.Member = None):
    target = membre or interaction.user
    db.ensure_player(target.id)
    p = db.get_player(target.id)

    embed = mk_embed(f"Profil de {target.display_name}", color=discord.Color.blurple())
    embed.add_field(name="Niveau (Elo)", value=str(round(p["elo"])), inline=True)
    embed.add_field(name="Matchs joues", value=str(p["matches_played"]), inline=True)
    embed.add_field(name="Victoires / Defaites", value=f"{p['wins']} / {p['losses']}", inline=True)
    embed.add_field(name="Serie de victoires", value=str(p["win_streak"]), inline=True)
    embed.add_field(name="Objectif", value=p["objectif"] or "non defini", inline=True)
    embed.add_field(name="Preference", value=p["preference"] or "non defini", inline=True)
    embed.add_field(name="Disponibilite", value=p["dispo"] or "non definie", inline=False)
    if target.avatar:
        embed.set_thumbnail(url=target.avatar.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="modifier-profil", description="Met a jour ton profil joueur.")
@app_commands.describe(
    objectif="Ton objectif principal",
    preference="Simple, double ou les deux",
    dispo="Tes disponibilites (ex: soirs et fins de semaine)",
    age="Ton age (optionnel)",
)
@app_commands.choices(objectif=OBJECTIF_CHOICES, preference=PREFERENCE_CHOICES)
async def modifier_profil(
    interaction: discord.Interaction,
    objectif: app_commands.Choice[str] = None,
    preference: app_commands.Choice[str] = None,
    dispo: str = None,
    age: int = None,
):
    db.update_player_profile(
        interaction.user.id,
        age=age,
        objectif=objectif.value if objectif else None,
        dispo=dispo,
        preference=preference.value if preference else None,
    )
    await interaction.response.send_message("Profil mis a jour. Utilise /profil pour le voir.", ephemeral=True)


# ---------- QUESTIONNAIRE NIVEAU ----------

YEARS_CHOICES = [
    app_commands.Choice(name="Je n'ai jamais joue", value="jamais"),
    app_commands.Choice(name="Moins de 1 an", value="moins_1"),
    app_commands.Choice(name="1 a 3 ans", value="1_3"),
    app_commands.Choice(name="3 a 10 ans", value="3_10"),
    app_commands.Choice(name="Plus de 10 ans", value="plus_10"),
]

NTRP_CHOICES = [
    app_commands.Choice(name="Debutant complet", value="debutant"),
    app_commands.Choice(name="Je me situe a 2.5-3.0", value="2.5_3"),
    app_commands.Choice(name="Je me situe a 3.5", value="3.5"),
    app_commands.Choice(name="Je me situe a 4.0", value="4.0"),
    app_commands.Choice(name="4.5 et plus", value="4.5_plus"),
]

FREQ_CHOICES = [
    app_commands.Choice(name="Je ne joue jamais", value="jamais"),
    app_commands.Choice(name="Rarement (quelques fois par an)", value="rarement"),
    app_commands.Choice(name="Regulierement (1-2x/semaine)", value="regulierement"),
    app_commands.Choice(name="Tres souvent (3x/semaine ou plus)", value="tres_souvent"),
]


@bot.tree.command(name="questionnaire", description="Estime ton niveau de depart (Elo) avec quelques questions.")
@app_commands.choices(annees=YEARS_CHOICES, niveau_ressenti=NTRP_CHOICES, frequence=FREQ_CHOICES)
async def questionnaire(
    interaction: discord.Interaction,
    annees: app_commands.Choice[str],
    niveau_ressenti: app_commands.Choice[str],
    frequence: app_commands.Choice[str],
):
    starting_elo = elo.estimate_starting_elo(annees.value, niveau_ressenti.value, frequence.value)
    p = db.get_player(interaction.user.id)
    if p and p["matches_played"] > 0:
        await interaction.response.send_message(
            "Tu as deja des matchs enregistres, ton niveau evolue maintenant avec tes resultats et ne peut plus etre re-estime.",
            ephemeral=True,
        )
        return
    db.set_elo(interaction.user.id, starting_elo)
    await interaction.response.send_message(
        f"Niveau de depart estime : **{starting_elo}** Elo. Il evoluera automatiquement apres chaque match confirme.",
        ephemeral=True,
    )


# ---------- RESULTATS DE MATCH ----------

class ConfirmMatchView(discord.ui.View):
    def __init__(self, match_id: int, opponent_id: int):
        super().__init__(timeout=86400)
        self.match_id = match_id
        self.opponent_id = opponent_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message(
                "Seul l'adversaire concerne peut confirmer ou contester ce match.", ephemeral=True
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

        new_winner_elo, new_loser_elo = elo.update_elo(winner_row["elo"], loser_row["elo"])
        db.set_elo(winner, new_winner_elo)
        db.set_elo(loser, new_loser_elo)
        db.record_match_result(
            p1, p2, winner, match["score"],
            winner_row["elo"] if winner == p1 else loser_row["elo"],
            winner_row["elo"] if winner == p2 else loser_row["elo"],
            new_winner_elo if winner == p1 else new_loser_elo,
            new_winner_elo if winner == p2 else new_loser_elo,
        )
        db.set_match_status(self.match_id, "confirmed")

        embed = mk_embed(
            "Match confirme !",
            f"Score : {match['score']}\n<@{winner}> remporte le match.\n\n"
            f"Nouveau elo gagnant : **{round(new_winner_elo)}**\n"
            f"Nouveau elo perdant : **{round(new_loser_elo)}**",
            color=discord.Color.green(),
        )
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Contester", style=discord.ButtonStyle.danger)
    async def contest(self, interaction: discord.Interaction, button: discord.ui.Button):
        db.set_match_status(self.match_id, "cancelled")
        embed = mk_embed("Match annule", "Les deux joueurs ne sont pas d'accord. Ce match ne compte pas dans le classement.", color=discord.Color.red())
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)


@bot.tree.command(name="resultat", description="Enregistre le resultat d'un match (l'adversaire doit confirmer).")
@app_commands.describe(adversaire="Ton adversaire", vainqueur="Qui a gagne le match", score="Le score (ex: 6-4 6-3)")
async def resultat(interaction: discord.Interaction, adversaire: discord.Member, vainqueur: discord.Member, score: str):
    if adversaire.id == interaction.user.id:
        await interaction.response.send_message("Tu ne peux pas jouer contre toi-meme.", ephemeral=True)
        return
    if vainqueur.id not in (interaction.user.id, adversaire.id):
        await interaction.response.send_message("Le vainqueur doit etre toi ou ton adversaire.", ephemeral=True)
        return

    db.ensure_player(interaction.user.id)
    db.ensure_player(adversaire.id)

    match_id = db.create_pending_match(interaction.user.id, adversaire.id, vainqueur.id, score)

    embed = mk_embed(
        "Resultat de match en attente de confirmation",
        f"<@{interaction.user.id}> declare : **<@{vainqueur.id}> gagne {score}** contre <@{adversaire.id if vainqueur.id == interaction.user.id else interaction.user.id}>.\n\n"
        f"<@{adversaire.id}>, confirme ou conteste ce resultat ci-dessous.",
        color=discord.Color.orange(),
    )
    view = ConfirmMatchView(match_id, adversaire.id)
    await interaction.response.send_message(content=f"<@{adversaire.id}>", embed=embed, view=view)


# ---------- CLASSEMENT ----------

@bot.tree.command(name="classement", description="Affiche le classement Elo du club.")
async def classement(interaction: discord.Interaction):
    rows = db.get_leaderboard(15)
    if not rows:
        await interaction.response.send_message("Aucun match confirme pour le moment.")
        return

    lines = []
    for i, row in enumerate(rows, start=1):
        user = await bot.fetch_user(int(row["user_id"]))
        lines.append(f"**{i}.** {user.display_name if hasattr(user, 'display_name') else user.name} - {round(row['elo'])} Elo ({row['wins']}V / {row['losses']}D)")

    embed = mk_embed("Classement du club", "\n".join(lines), color=discord.Color.gold())
    await interaction.response.send_message(embed=embed)


# ---------- HISTORIQUE ----------

@bot.tree.command(name="historique", description="Affiche tes derniers matchs confirmes.")
async def historique(interaction: discord.Interaction, membre: discord.Member = None):
    target = membre or interaction.user
    rows = db.get_match_history(target.id, limit=5)
    if not rows:
        await interaction.response.send_message(f"{target.display_name} n'a pas encore de match confirme.")
        return

    lines = []
    for row in rows:
        winner_mention = f"<@{row['winner_id']}>"
        lines.append(f"{row['created_at'][:10]} - Score {row['score']} - Vainqueur : {winner_mention}")

    embed = mk_embed(f"Historique de {target.display_name}", "\n".join(lines))
    await interaction.response.send_message(embed=embed)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("BOT_TOKEN manquant dans .env")
    bot.run(TOKEN)
