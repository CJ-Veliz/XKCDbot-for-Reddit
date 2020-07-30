import os
import requests
from bs4 import BeautifulSoup
import re
import config
import time
import mysql.connector
import logging

class XKCD_bot:
    def __init__(self):
        self.additional_comments = []
        self.rate_limit_used = 0
        self.rate_limit_remaining = 60
        self.rate_limit_reset = 60
        self.request_count = 0
        self.scan_count = 0

        self.database = mysql.connector.connect(
                host= config.dbHost,
                user=config.dbUser,
                password=config.dbPassword,
                database=config.database
                )
        cursor = self.database.cursor()
        cursor.execute("SELECT parent_id FROM posts")
        self.posts_replied_to = [i[0] for i in cursor.fetchall()]

        logging.basicConfig(filename="bot_log.log", filemode='a', format="%(asctime)s %(levelname)-4s: %(message)s", datefmt='%Y-%m-%d %H:%M:%S', level=logging.INFO)

    def authorize(self) -> dict:
        client_auth = requests.auth.HTTPBasicAuth(config.CLIENT_ID, config.CLIENT_SECRET)
        post_data = {'grant_type': 'password',
                    "username": config.USERNAME, "password": config.PASSWORD}

        header = {'User-Agent': config.USER_AGENT}
        authorization_response = requests.post("https://www.reddit.com/api/v1/access_token",
                                                auth= client_auth, data= post_data, headers= header)

        authorization_response = authorization_response.json()

        header.update({ "Authorization": f"{authorization_response['token_type']} {authorization_response['access_token']}" })

        return header


    def get_top_level_comments(self, subreddit: str, article_id: str, headers: dict) -> list:
        top_level_parameters = {'article': article_id, 'depth': 1,
                                'showedits' : True, 'showmore' : True,
                                'sort': 'best', 'threaded' : False}

        top_level_response = self.api_get_request(f"https://oauth.reddit.com/r/{subreddit}/comments/{article_id}/.json", top_level_parameters, headers)

        comments = top_level_response.json()[1]['data']['children']
        comment_list = []

        for comment in comments:
            if comment['kind'] == 't1':
                comment_list.append(comment['data']['id'])
            elif comment['kind'] == 'more':
                self.additional_comments.extend(comment['data']['children'])

        return comment_list


    def scan_submission(self, subreddit: str, article_id: str):
        parameters = {'article': article_id, 'showedits' : True,
                    'showmore' : False, 'sort': 'best',
                    'threaded' : True}
        headers = self.authorize()

        for top_level_comment_id in self.get_top_level_comments(subreddit, article_id, headers):
            parameters['comment'] = top_level_comment_id

            response = self.api_get_request(f"https://oauth.reddit.com/r/{subreddit}/comments/{article_id}/", parameters, headers)
            listing = response.json()[1]

            top_level_comment = listing['data']['children'][0]['data']
            self.scan_comment_text_and_reply(top_level_comment, headers)
            self.traverse_comment_replies(top_level_comment, headers)

            if self.additional_comments:
                self.resolve_more_comments(subreddit, article_id, headers)


    def traverse_comment_replies(self, comment_data: dict, headers: dict):
        replies_listing = comment_data['replies']

        if replies_listing:
            for child in replies_listing['data']['children']:#.reverse():

                if child['kind'] == 't1':
                    self.scan_comment_text_and_reply(child['data'], headers)
                    self.traverse_comment_replies(child['data'], headers)

                elif child['kind'] == 'more':
                    self.additional_comments.extend(child['data']['children'])


    # (100) is the allowed maximum amount of comments the reddit api will return per call
    def resolve_more_comments(self, subreddit: str, article_id: str, headers: dict):
        comment_ids = ",".join(self.additional_comments[: 100])
        self.additional_comments = self.additional_comments[100 :]

        if comment_ids:
            parameters = {'api_type': 'json', 'link_id': f"t3_{article_id}", 'children': comment_ids}

            more_comments = self.api_get_request("https://api.reddit.com/api/morechildren.json", parameters, {'User-Agent': headers['User-Agent']})
            more_comments = more_comments.json()['json']['data']['things']

            for comment in more_comments:
                if comment['kind'] == "t1":
                    self.scan_comment_text_and_reply(comment['data'], headers)
                elif comment['kind'] == "more":
                    self.additional_comments.extend(comment['data']['children'])


    def scan_comment_text_and_reply(self, comment_data: dict, headers: dict):
        self.scan_count += 1
        regex_scan = re.search("(https://xkcd\\.com/)(\\d{1,4})(?:\\s|/|\\)|$)", comment_data['body'])

        if regex_scan and comment_data['id'] not in self.posts_replied_to:

            parameters = {'api_type': 'json', 'thing_id': comment_data['name']}
            parameters['text'] = "Comic Title Text: **" + \
            self.get_comic_title_text( regex_scan.group(2) ) + "**\n\n---\n^(Made for mobile users, to easily see xkcd comic's title text)"

            if self.rate_limit_remaining <= 0:
                time.sleep(self.rate_limit_reset)

            response = requests.post(url= "https://oauth.reddit.com/api/comment",
                                        params= parameters, headers= headers)
            if response.ok:
                response = response.json()

                if response['json']['errors']:
                    logging.error("POST request application error, %s", response['json']['errors'])
                else:
                    posted_comment_data = response['json']['data']['things'][0]['data']
                    logging.info("POST %s, %s", posted_comment_data['name'], posted_comment_data['subreddit'])
                    self.db_insert_post(posted_comment_data, regex_scan.group(2))

            else:
                logging.error("POST response error, <%s>, %s", response, response.text)



    def get_comic_title_text(self, xkcd_number: str) -> str:
        comic_page = BeautifulSoup(requests.get(url=f"https://xkcd.com/{xkcd_number}/").text, 'html.parser')

        comic = comic_page.find(id='comic')
        return comic.find('img')['title']


    def api_get_request(self, api_url: str, request_paramaters: dict, request_headers: dict) -> requests.models.Response:
        if self.rate_limit_remaining <= 0:
            print(f"\nLIMIT EXCEEDED, waiting: {self.rate_limit_reset} seconds\n")
            time.sleep(self.rate_limit_reset)

        api_response = requests.get(url= api_url, params= request_paramaters, headers= request_headers)
        self.request_count += 1

        if api_response.ok:
            if request_headers.get("Authorization"):
                self.rate_limit_reset = int(api_response.headers['x-ratelimit-reset'])
                self.rate_limit_remaining = int(float(api_response.headers['x-ratelimit-remaining']))
                self.rate_limit_used = int(api_response.headers['x-ratelimit-used'])

            print(f"https request sent: {api_url}")
            print(f"rate limits: {self.rate_limit_used} used with {self.rate_limit_remaining} remaining. {self.rate_limit_reset} seconds to reset")
        else:
            logging.critical("ERROR, <%s> %s", api_response, api_response.text)

        return api_response


    def monitor_subreddit_hot25(self, subreddit: str):
        header = {'User-Agent': config.USER_AGENT}
        parameters = {'limit': 25}
        subreddit_hot_listing = self.api_get_request(f"https://www.reddit.com/r/{subreddit}/hot/.json", parameters, header)

        for submission in subreddit_hot_listing.json()['data']['children']:
            self.scan_submission(subreddit, submission['data']['id'])

        print(f"finished monitoring /r/{subreddit}/ with {self.request_count} reddit api requests and {self.scan_count} comments scanned")


    def db_insert_post(self, post_comment: dict, xkcd_number: str):
        cursor = self.database.cursor()
        query = f"INSERT INTO posts (parent_id, post_id, subreddit, commic) VALUES ('{post_comment['parent_id'][3:]}', '{post_comment['id']}', '{post_comment['subreddit']}', '{xkcd_number}')"
        cursor.execute(query)



xkcd = XKCD_bot()
# xkcd.scan_submission("test", "hvfakc")
# xkcd.scan_submission("dataisbeautiful", "hs9mnz")
# xkcd.scan_submission("ProgrammerHumor", "hy1piz")
xkcd.monitor_subreddit_hot25('ProgrammerHumor')

while xkcd.additional_comments:
    xkcd.resolve_more_comments

xkcd.database.commit()