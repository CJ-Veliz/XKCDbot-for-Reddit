import os
import requests
from bs4 import BeautifulSoup
import re
import config

class XKCD_bot:

    def __init__(self):
        self.additional_comments = []
        self.rate_limit_used = 0
        self.rate_limit_remaining = 60
        self.rate_limit_reset = 0

        if not os.path.isfile("posts_replied_to.txt"):
            self.posts_replied_to = []
        else:
            with open("posts_replied_to.txt", 'r') as f:
                self.posts_replied_to = f.read()
                self.posts_replied_to = self.posts_replied_to.split("\n")
                self.posts_replied_to = list(filter(None, self.posts_replied_to))

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

        top_level_response = requests.get(url= f"https://oauth.reddit.com/r/{subreddit}/comments/{article_id}/.json",
                                            params= top_level_parameters, headers= headers)

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

            response = requests.get(url= f"https://oauth.reddit.com/r/{subreddit}/comments/{article_id}/",
                                    params= parameters, headers= headers)
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


    def resolve_more_comments(self, subreddit: str, article_id: str, headers: dict):
        comment_ids = ",".join(self.additional_comments[: 100])
        self.additional_comments = self.additional_comments[100 :]

        parameters = {'api_type': 'json', 'link_id': f"t3_{article_id}", 'children': comment_ids}

        more_comments = requests.get(url="https://api.reddit.com/api/morechildren.json",
                                        params= parameters, headers= headers)
        more_comments = more_comments.json()['data']['things']

        for comment in more_comments:
            if comment['kind'] == "t1":
                self.scan_comment_text_and_reply(comment['data'], headers)
            elif comment['kind'] == "more":
                self.additional_comments.extend(comment['data']['children'])


    def scan_comment_text_and_reply(self, comment_data: dict, headers: dict):

        # regex_scan = re.search( "https://xkcd\\.com/\\d{1,4}(?:/| |\\n)", comment_data['body'])

        # if regex_scan:
        #     print("\n", "---------------------text found---------------------", "\n")
        #     print(regex_scan.group(0))

        #     if comment_data['id'] not in self.posts_replied_to:
        #         parameters = {'api_type': 'json', 'thing_id': comment_data['name']}

        #         parameters['text'] = "Comic Alt/Title Text:" + \
        #         self.get_comic_title_text(regex_scan.group(0)) + "\n\n---\n^(Made for mobile users, to easily see xkcd comic's title text )"

        #         response = requests.post(url= "https://oauth.reddit.com/api/comment",
        #                                     params= parameters, headers= headers)

        #         print(response.text)
        #         self.posts_replied_to.append(comment_data['id'])

        # TODO: regex
    def get_comic_title_text(self, xkcd_url: str) -> str:
        comic_page = BeautifulSoup(requests.get(url= xkcd_url).text, 'html.parser')

        comic = comic_page.find(id='comic')
        return comic.find('img')['title']


    # def api_get_request():
    #     pass



xkcd = XKCD_bot()
xkcd.scan_submission("test", "hvfakc")

# while xkcd.additional_comments:
#     xkcd.resolve_more_comments

with open("posts_replied_to.txt", "w") as f:
    for post_id in xkcd.posts_replied_to:
        f.write(post_id + "\n")