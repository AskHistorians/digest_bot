from dotenv import load_dotenv
import logging
import os
import praw
import prawcore
import sqlite3
import sys
import datetime
import time

class DigestBot:
    def __init__(self):
        self.reddit = self.reddit_init()
        self.db = self.create_database()
        self.backup = self.setup_backup()
        self.last_backup = None

        if os.getenv("AHDEBUG") in ["TRUE", "true"]:
            logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
        else:
            logging.basicConfig(stream=sys.stdout, level=logging.INFO)

    def reddit_init(self):
        load_dotenv()
        username = os.getenv("REDDIT_USERNAME")
        password = os.getenv("REDDIT_PASSWORD")
        client_id = os.getenv("REDDIT_CLIENT_ID")
        client_secret = os.getenv("REDDIT_SECRET")
        user_agent = "DigestBot:v1.0 (by u/AverageAngryPeasant)"
        return praw.Reddit(client_id=client_id, client_secret=client_secret, user_agent=user_agent, username=username, password=password)

    def create_database(self):
        db_path = os.getenv("DIGEST_BOT_DB_PATH")
        exists = os.path.isfile(db_path)
        db = sqlite3.connect(db_path)
        c = db.cursor()
        if not exists:
            c.execute("CREATE TABLE SUBS ([user] text, [mod] integer)")
        db.commit()
        return db

    def setup_backup(self):
        path = os.path.join(os.path.dirname(os.getenv("DIGEST_BOT_DB_PATH")), "ah_backups")
        if not os.path.isdir(path):
            os.mkdir(path)
            logging.info(f"Created backup folder in {path}")
        return path
            
    def create_backup(self):
        logging.info("Starting to create backup.")
        name = datetime.datetime.now().strftime("%Y-%m-%d-%H:%M:%S") + "-backup.db"
        self.last_backup = datetime.datetime.now()

        back = sqlite3.connect(os.path.join(self.backup, name))
        with back:
            self.db.backup(back)
        back.close()
        logging.info("Backup finished!")
        self.delete_backups()

    def extract_command(self, text):
        logging.info("Recieved message with text:")
        logging.info(text)
        text = text.strip()
        split = text.split()
        if split:
            command = split[0]
            text = text[len(command):].strip()
            return command, text
        else:
            return "", text

    def parse_message(self, message):
        command, text = self.extract_command(message.body)
        subject = message.subject
        logging.info(f"Parsed message with command {command} and text {text}.")
        user = message.author.name

        if user in ["reddit"]: # automated emails
            return

        if command in ["!sub", "!subscribe"]:
            self.add_user(user)
        elif command in ["!unsub", "!unsubscribe"]:
            self.remove_user(user)
        elif command in ["!mod"]:
            self.mod_user(user, text)
        elif command in ["!unmod"]:
            self.unmod_user(user, text)
        elif command in ["!send"]:
            if self.check_mod(user):
                if not text:
                    self.send_pm(user, "AH Digest", "Error when sending digest: must include message.")
                else:
                    self.send_digest(user, subject, text)
            else:
                self.send_pm(user, "AH Digest", "You are not authorized to perform this operation.")
        elif command in ["!export_mods"]:
            self.export_mods(user)
        else:
            pass
            # text = message.body.strip()
            # user = message.author.name
            # self.message_owner(user, subject, text)

    def fetch_mods(self):
        c = self.db.cursor()
        c.execute("SELECT user FROM subs where mod = 1")
        return [user[0] for user in c.fetchall()]

    def check_user(self, user):
        c = self.db.cursor()
        c.execute("SELECT user FROM subs where user = ?", [user])
        return c.fetchone() != None

    def check_mod(self, user):
        if user in ["AverageAngryPeasant", "Georgy_K_Zhukov", "AHMessengerBot"]:
            return True

        c = self.db.cursor()
        c.execute("SELECT user FROM subs where user = ? AND mod = 1", [user])
        result = c.fetchone()
        return result != None

    def add_user(self, user):
        if self.check_user(user):
            self.send_pm(user, "AH Digest", "You are already subbed.")
            logging.info(f"Attempted add failed, {user} is already subbed.")
            return

        c = self.db.cursor()
        c.execute("INSERT INTO SUBS VALUES (?, 0)", [user])
        self.db.commit()
        self.send_pm(user, "AH Digest", "Added to AH Digest successfully!")
        logging.info(f"Added user {user} successfully.")

    def remove_user(self, user):
        if not self.check_user(user):
            self.send_pm(user, "AH Digest", "You are already not subbed.")
            logging.info(f"Attempted remove failed, {user} is already not subbed.")
            return

        c = self.db.cursor()
        c.execute("DELETE FROM SUBS WHERE user = ?", [user])
        self.db.commit()
        self.send_pm(user, "AH Digest", "Removed from AH Digest successfully!")
        logging.info(f"Removed user {user} successfully.")

    def mod_user(self, user, text):
        if not self.check_mod(user):
            self.send_pm(user, "AH Digest", "You are not authorized to perform this operation.")
            logging.info(f"Attempted mod failed, {user} is not modded.")
            return
        
        if not text:
            text = user

        c = self.db.cursor()
        c.execute("UPDATE subs SET mod = 1 WHERE user = ?", [text])
        self.db.commit()
        self.send_pm(user, "AH Digest", "Modded to AH Digest successfully!")
        if text != user:
            self.send_pm(text, "AH Digest", f"You have been modded by user {user}.")
        logging.info(f"Mod {user} modded user {text} successfully.")

    def unmod_user(self, user, text):
        if not self.check_mod(user):
            self.send_pm(user, "AH Digest", "You are not authorized to perform this operation.")
            logging.info(f"Attempted unmod failed, {user} is not modded.")
            return

        if not text:
            text = user

        c = self.db.cursor()
        c.execute("UPDATE subs SET mod = 0 WHERE user = ?", [text])
        self.db.commit()
        self.send_pm(user, "AH Digest", "Unmodded from AH Digest successfully!")
        if text != user:
            self.send_pm(text, "AH Digest", f"You have been unmodded by user {user}.")
        logging.info(f"Mod {user} unmodded user {text} successfully.")

    def send_digest(self, user, subject, text):
        c = self.db.cursor()
        subs = c.execute("SELECT user FROM subs")
        errors = [[], [], [], []]
        count = 0
        err_msgs = ["Non-whitelisted users: ", "Nonexistent users: ", "Server errors: ", "Other errors: "]

        for sub in subs:
            sub = sub[0]
            self.last_user = sub

            try:
                self.send_pm(sub, subject, text)
            except praw.exceptions.RedditAPIException as err:
                if err.error_type == 'NOT_WHITELISTED_BY_USER_MESSAGE':
                    logging.error(f"Non-whitelisted user {sub}: " + str(err))
                    errors[0].append(sub)
                elif err.error_type == 'USER_DOESNT_EXIST':
                    logging.error("Non-existent user found: " + str(err))
                    logging.info("Deleting non-existent user.")
                    c = self.db.cursor()
                    c.execute("DELETE FROM SUBS WHERE user = ?", [sub])
                    self.db.commit()
                    logging.info("User successfully deleted.")
                    errors[1].append(sub)
                else:
                    logging.error("Reddit API Exception: " + str(err))
                    errors[-1].append(sub)
            except prawcore.exceptions.ServerError as err:
                logging.error(f"Server Error on user {sub}: " + str(err))
                errors[2].append(sub)
            else:
                count += 1

        confirm = "Sent AH Digest successfully!"
        confirm += f"\n\nSuccessfully sent message to {count} users."
        for i in range(len(errors)):
            if errors[i]:
                confirm += "\n\n" + err_msgs[i] + str(len(errors[i]))
        self.send_pm(user, "AH Digest", confirm)
        logging.info(f"Successfully sent digest.")
        logging.info(f"Digest had subject {subject} and text {text}.")

    def send_pm(self, user, subject, text):
        self.reddit.redditor(user).message(subject, text)

    def message_owner(self, user, subject, text):
        if text and text not in ["sub", "subscribe", "unsub", "unsubscribe", "mod", "unmod", "send"] and text[0] != "!":
            text = "User " + user + " has sent you a message through DigestBot:\n\n" + "SUBJECT: " + subject + "\n\n" + text
            self.send_pm("AverageAngryPeasant", "DigestBot PM", text)
        logging.info(f"Private message sent by user {user}.")

    def export_mods(self, user):
        if not self.check_mod(user):
            logging.info(f"Attempted mod export failed, {user} is not modded.")
            return

        mods = self.fetch_mods()
        users = '\n\n'.join(mods)
        if not users:
            users = "There are no currently modded users."
        self.send_pm(user, "List of AHMessengerBot mods", users)
        logging.info(f"Sent {user} list of mods successfully.")

    def print_db(self):
        c = self.db.cursor()
        c.execute("SELECT * FROM SUBS")
        logging.info(c.fetchall())

    def delete_backups(self, keep=10):
        mtime = lambda f: os.stat(os.path.join(self.backup, f)).st_mtime
        files = list(sorted(os.listdir(self.backup), key=mtime))
        del_list = files[0:(len(sorted_ls(path))-keep)]
        
        for dfile in del_list:
            os.remove(os.path.join(self.backup, dfile))
        logging.info(f"Successfully removed {len(del_list)} old backup files!")

    def main(self):
        self.print_db()
        while True:
            try:
                for message in self.reddit.inbox.stream():
                    if not self.last_backup or self.last_backup + datetime.timedelta(hours=12) < datetime.datetime.now():
                        self.create_backup()
                    self.parse_message(message)
                    message.mark_read()
            except sqlite3.DatabaseError as err:
                logging.error("Sqlite error: " + str(err))
            except prawcore.exceptions.ResponseException as err:
                logging.error("Bad response: " + str(err))
                time.sleep(60)
                logging.error("Woke up from sleep.")

if __name__ == "__main__":
    bot = DigestBot()
    bot.main()
