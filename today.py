import datetime
from dateutil import relativedelta
import requests
import os
from lxml import etree
import time
import hashlib
import re

HEADERS = {'authorization': 'token ' + os.environ['ACCESS_TOKEN']}
USER_NAME = os.environ['USER_NAME']
QUERY_COUNT = {'user_getter': 0, 'follower_getter': 0, 'graph_repos_stars': 0, 'recursive_loc': 0, 'graph_commits': 0,
               'loc_query': 0}


def daily_readme(birthday):
    diff = relativedelta.relativedelta(datetime.datetime.today(), birthday)
    return '{} {}, {} {}, {} {}{}'.format(
        diff.years, 'year' + format_plural(diff.years),
        diff.months, 'month' + format_plural(diff.months),
        diff.days, 'day' + format_plural(diff.days),
        ' 🎂' if (diff.months == 0 and diff.days == 0) else '')


def format_plural(unit):
    return 's' if unit != 1 else ''


def simple_request(func_name, query, variables):
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables': variables},
                            headers=HEADERS)
    if request.status_code == 200:
        return request
    raise Exception(func_name, ' has failed with a', request.status_code, request.text, QUERY_COUNT)


def graph_commits():
    """Fetch all-time commits from 2020-01-01 to now and current year commits"""
    query_count('graph_commits')
    
    # Calculate total commits across all years from 2020 to now
    total_commits = 0
    current_year = datetime.datetime.now().year
    year_commits = 0
    
    for year in range(2020, current_year + 1):
        # Set date range for each year
        from_date = f"{year}-01-01T00:00:00Z"
        to_date = f"{year}-12-31T23:59:59Z" if year < current_year else datetime.datetime.now().isoformat() + "Z"
        
        query = '''
        query($login: String!, $from: DateTime!, $to: DateTime!) {
            user(login: $login) {
                contributionsCollection(from: $from, to: $to) {
                    contributionCalendar {
                        totalContributions
                    }
                }
            }
        }'''
        variables = {'login': USER_NAME, 'from': from_date, 'to': to_date}
        request = simple_request(graph_commits.__name__, query, variables)
        data = request.json()['data']['user']['contributionsCollection']
        commits = int(data['contributionCalendar']['totalContributions'])
        total_commits += commits
        
        # Track current year separately
        if year == current_year:
            year_commits = commits
    
    return total_commits, year_commits


def get_streak_stats():
    """Fetch contribution streak statistics from all years (2020 to present)"""
    query_count('graph_commits')
    
    current_year = datetime.datetime.now().year
    all_days = []
    
    # Fetch contribution data from 2020 to present
    for year in range(2020, current_year + 1):
        from_date = f"{year}-01-01T00:00:00Z"
        to_date = f"{year}-12-31T23:59:59Z" if year < current_year else datetime.datetime.now().isoformat() + "Z"
        
        query = '''
        query($login: String!, $from: DateTime!, $to: DateTime!) {
            user(login: $login) {
                contributionsCollection(from: $from, to: $to) {
                    contributionCalendar {
                        weeks {
                            contributionDays {
                                contributionCount
                                date
                            }
                        }
                    }
                }
            }
        }'''
        variables = {'login': USER_NAME, 'from': from_date, 'to': to_date}
        request = simple_request(get_streak_stats.__name__, query, variables)
        
        weeks = request.json()['data']['user']['contributionsCollection']['contributionCalendar']['weeks']
        
        # Flatten days from weeks
        for week in weeks:
            all_days.extend(week['contributionDays'])
    
    # Sort days by date to ensure proper order
    all_days.sort(key=lambda x: x['date'])
    
    # Calculate streaks
    current_streak = 0
    longest_streak = 0
    temp_streak = 0
    
    # Reverse to check from today backwards for current streak
    today = datetime.datetime.now().date()
    
    # Check current streak (must include today or yesterday)
    for day in reversed(all_days):
        day_date = datetime.datetime.fromisoformat(day['date'].replace('Z', '+00:00')).date()
        if day_date > today:
            continue
            
        if day['contributionCount'] > 0:
            current_streak += 1
        else:
            # If we haven't started counting yet (checking today/yesterday)
            days_diff = (today - day_date).days
            if days_diff <= 1:
                continue
            else:
                break
    
    # Calculate longest streak across all years
    for day in all_days:
        if day['contributionCount'] > 0:
            temp_streak += 1
            longest_streak = max(longest_streak, temp_streak)
        else:
            temp_streak = 0
    
    return {
        'current_streak': current_streak,
        'longest_streak': longest_streak
    }


def graph_repos_stars(count_type, owner_affiliation, cursor=None):
    query_count('graph_repos_stars')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            stargazers {
                                totalCount
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(graph_repos_stars.__name__, query, variables)
    if request.status_code == 200:
        if count_type == 'repos':
            return request.json()['data']['user']['repositories']['totalCount']
        elif count_type == 'stars':
            return stars_counter(request.json()['data']['user']['repositories']['edges'])


def recursive_loc(owner, repo_name, data, cache_comment, addition_total=0, deletion_total=0, my_commits=0, cursor=None):
    query_count('recursive_loc')
    query = '''
    query ($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            totalCount
                            edges {
                                node {
                                    ... on Commit {
                                        committedDate
                                    }
                                    author {
                                        user {
                                            id
                                        }
                                    }
                                    deletions
                                    additions
                                }
                            }
                            pageInfo {
                                endCursor
                                hasNextPage
                            }
                        }
                    }
                }
            }
        }
    }'''
    variables = {'repo_name': repo_name, 'owner': owner, 'cursor': cursor}
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables': variables},
                            headers=HEADERS)
    if request.status_code == 200:
        if request.json()['data']['repository']['defaultBranchRef'] != None:
            return loc_counter_one_repo(owner, repo_name, data, cache_comment,
                                        request.json()['data']['repository']['defaultBranchRef']['target']['history'],
                                        addition_total, deletion_total, my_commits)
        else:
            return 0
    force_close_file(data, cache_comment)
    if request.status_code == 403:
        raise Exception(
            'Too many requests in a short amount of time!\nYou\'ve hit the non-documented anti-abuse limit!')
    raise Exception('recursive_loc() has failed with a', request.status_code, request.text, QUERY_COUNT)


def loc_counter_one_repo(owner, repo_name, data, cache_comment, history, addition_total, deletion_total, my_commits):
    for node in history['edges']:
        if node['node']['author']['user'] == OWNER_ID:
            my_commits += 1
            addition_total += node['node']['additions']
            deletion_total += node['node']['deletions']

    if history['edges'] == [] or not history['pageInfo']['hasNextPage']:
        return addition_total, deletion_total, my_commits
    else:
        return recursive_loc(owner, repo_name, data, cache_comment, addition_total, deletion_total, my_commits,
                             history['pageInfo']['endCursor'])


def loc_query(owner_affiliation, comment_size=0, force_cache=False, cursor=None, edges=[]):
    query_count('loc_query')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
            edges {
                node {
                    ... on Repository {
                        nameWithOwner
                        defaultBranchRef {
                            target {
                                ... on Commit {
                                    history {
                                        totalCount
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(loc_query.__name__, query, variables)
    if request.json()['data']['user']['repositories']['pageInfo']['hasNextPage']:
        edges += request.json()['data']['user']['repositories']['edges']
        return loc_query(owner_affiliation, comment_size, force_cache,
                         request.json()['data']['user']['repositories']['pageInfo']['endCursor'], edges)
    else:
        return cache_builder(edges + request.json()['data']['user']['repositories']['edges'], comment_size, force_cache)


def cache_builder(edges, comment_size, force_cache, loc_add=0, loc_del=0):
    edges = [e for e in edges if e is not None and e.get('node') is not None]
    cached = True
    filename = 'cache/' + hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest() + '.txt'
    try:
        with open(filename, 'r') as f:
            data = f.readlines()
    except FileNotFoundError:
        data = []
        if comment_size > 0:
            for _ in range(comment_size): data.append('This line is a comment block. Write whatever you want here.\n')
        with open(filename, 'w') as f:
            f.writelines(data)

    if len(data) - comment_size != len(edges) or force_cache:
        cached = False
        flush_cache(edges, filename, comment_size)
        with open(filename, 'r') as f:
            data = f.readlines()

    cache_comment = data[:comment_size]
    data = data[comment_size:]
    for index in range(len(edges)):
        repo_hash, commit_count, *__ = data[index].split()
        if repo_hash == hashlib.sha256(edges[index]['node']['nameWithOwner'].encode('utf-8')).hexdigest():
            try:
                if int(commit_count) != edges[index]['node']['defaultBranchRef']['target']['history']['totalCount']:
                    owner, repo_name = edges[index]['node']['nameWithOwner'].split('/')
                    loc = recursive_loc(owner, repo_name, data, cache_comment)
                    data[index] = repo_hash + ' ' + str(
                        edges[index]['node']['defaultBranchRef']['target']['history']['totalCount']) + ' ' + str(
                        loc[2]) + ' ' + str(loc[0]) + ' ' + str(loc[1]) + '\n'
            except TypeError:
                data[index] = repo_hash + ' 0 0 0 0\n'
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    for line in data:
        loc = line.split()
        loc_add += int(loc[3])
        loc_del += int(loc[4])
    return [loc_add, loc_del, loc_add - loc_del, cached]


def flush_cache(edges, filename, comment_size):
    with open(filename, 'r') as f:
        data = []
        if comment_size > 0:
            data = f.readlines()[:comment_size]
    with open(filename, 'w') as f:
        f.writelines(data)
        for node in edges:
            if node.get('node') is not None and node['node'].get('nameWithOwner') is not None:
                f.write(hashlib.sha256(node['node']['nameWithOwner'].encode('utf-8')).hexdigest() + ' 0 0 0 0\n')


def force_close_file(data, cache_comment):
    filename = 'cache/' + hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest() + '.txt'
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    print('There was an error while writing to the cache file. The file,', filename,
          'has had the partial data saved and closed.')


def stars_counter(data):
    total_stars = 0
    for node in data: total_stars += node['node']['stargazers']['totalCount']
    return total_stars


def committers_rank_getter(username, country='iraq'):
    # Try multiple endpoints to get a valid rank
    endpoints = [
        f"https://user-badge.committers.top/{country}_private/{username}.svg",
        f"https://user-badge.committers.top/{country}/{username}.svg",
        f"https://user-badge.committers.top/worldwide/{username}.svg"
    ]
    
    for url in endpoints:
        try:
            response = requests.get(url, timeout=15)
            if response.status_code == 200:
                rank = extract_rank_from_committers_svg(response.text)
                if rank != 'Unranked':
                    return rank
        except:
            continue
    
    return 'Unranked'




def extract_rank_from_committers_svg(svg_text):
    if re.search(r"\bunranked\b", svg_text, flags=re.IGNORECASE):
        return 'Unranked'

    match = re.search(r"#\s*([0-9][0-9,]*)", svg_text)
    if match:
        return int(match.group(1).replace(',', ''))

    try:
        root = etree.fromstring(svg_text.encode('utf-8'))
        texts = [t for t in root.itertext() if t is not None]
        normalized = ' '.join(' '.join(texts).split())
    except Exception:
        normalized = svg_text

    match = re.search(r"\b(?:rank|ranking)\b\D{0,30}([0-9][0-9,]*)", normalized, flags=re.IGNORECASE)
    if match:
        return int(match.group(1).replace(',', ''))

    match = re.search(r"\b([0-9][0-9,]*)\b", normalized)
    if match:
        return int(match.group(1).replace(',', ''))

    raise ValueError('Could not extract rank from committers.top SVG')


def svg_overwrite(filename, age_data, commit_data, year_commits, rank_data, repo_data, contrib_data, follower_data, loc_data, top_langs, streak_stats):
    tree = etree.parse(filename)
    root = tree.getroot()
    justify_format(root, 'age_data', age_data, 90)
    justify_format(root, 'commit_data', commit_data, 39)
    justify_format(root, 'year_commits', year_commits, 0)
    justify_format(root, 'rank_data', rank_data, 21)
    justify_format(root, 'repo_data', repo_data, 24)
    justify_format(root, 'contrib_data', contrib_data, 0)
    justify_format(root, 'follower_data', follower_data, 36)
    justify_format(root, 'loc_data', loc_data[2], 49)
    justify_format(root, 'loc_add', loc_data[0], 0)
    justify_format(root, 'loc_del', loc_data[1], 0)
    
    # Update streak stats
    justify_format(root, 'current_streak', streak_stats['current_streak'], 0)
    justify_format(root, 'longest_streak', streak_stats['longest_streak'], 0)
    
    # Update top languages
    for i, lang in enumerate(top_langs[:5]):
        justify_format(root, f'lang{i+1}_name', lang['name'], 0)
        justify_format(root, f'lang{i+1}_pct', f"{lang['percentage']}%", 0)
    
    tree.write(filename, encoding='utf-8', xml_declaration=True)


def justify_format(root, element_id, new_text, total_width):
    if isinstance(new_text, int):
        new_text = f"{'{:,}'.format(new_text)}"
    new_text = str(new_text)
    find_and_replace(root, element_id, new_text)

    if total_width > 0:
        dots_needed = total_width - len(new_text)
        if dots_needed <= 0:
            dot_string = ''
        elif dots_needed == 1:
            dot_string = ' '
        elif dots_needed == 2:
            dot_string = '. '
        else:
            dot_string = ' ' + ('.' * (dots_needed - 2)) + ' '
        find_and_replace(root, f"{element_id}_dots", dot_string)


def find_and_replace(root, element_id, new_text):
    element = root.find(f".//*[@id='{element_id}']")
    if element is not None:
        element.text = new_text


def user_getter(username):
    query_count('user_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            id
            createdAt
        }
    }'''
    variables = {'login': username}
    request = simple_request(user_getter.__name__, query, variables)
    return {'id': request.json()['data']['user']['id']}, request.json()['data']['user']['createdAt']


def follower_getter(username):
    query_count('follower_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            followers {
                totalCount
            }
        }
    }'''
    request = simple_request(follower_getter.__name__, query, {'login': username})
    return int(request.json()['data']['user']['followers']['totalCount'])


def top_languages_getter(username):
    query_count('follower_getter')
    query = '''
    query($login: String!) {
        user(login: $login) {
            repositories(first: 100, ownerAffiliations: OWNER, orderBy: {field: STARGAZERS, direction: DESC}) {
                nodes {
                    languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
                        edges {
                            size
                            node {
                                name
                                color
                            }
                        }
                    }
                }
            }
        }
    }'''
    request = simple_request(top_languages_getter.__name__, query, {'login': username})
    
    # Aggregate language data
    lang_totals = {}
    lang_colors = {}
    
    for repo in request.json()['data']['user']['repositories']['nodes']:
        for edge in repo['languages']['edges']:
            lang_name = edge['node']['name']
            lang_size = edge['size']
            lang_color = edge['node']['color'] or '#858585'
            
            if lang_name in lang_totals:
                lang_totals[lang_name] += lang_size
            else:
                lang_totals[lang_name] = lang_size
                lang_colors[lang_name] = lang_color
    
    # Sort by size and get top 5
    sorted_langs = sorted(lang_totals.items(), key=lambda x: x[1], reverse=True)[:5]
    total_size = sum(lang_totals.values())
    
    # Calculate percentages
    result = []
    for lang, size in sorted_langs:
        percentage = (size / total_size * 100) if total_size > 0 else 0
        result.append({
            'name': lang,
            'percentage': round(percentage, 1),
            'color': lang_colors[lang]
        })
    
    return result


def query_count(funct_id):
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1


def perf_counter(funct, *args):
    start = time.perf_counter()
    funct_return = funct(*args)
    return funct_return, time.perf_counter() - start


def formatter(query_type, difference, funct_return=False, whitespace=0):
    print('{:<23}'.format('   ' + query_type + ':'), sep='', end='')
    print('{:>12}'.format('%.4f' % difference + ' s ')) if difference > 1 else print(
        '{:>12}'.format('%.4f' % (difference * 1000) + ' ms'))
    if whitespace:
        return f"{'{:,}'.format(funct_return): <{whitespace}}"
    return funct_return


if __name__ == '__main__':
    print('Calculation times:')
    user_data, user_time = perf_counter(user_getter, USER_NAME)
    OWNER_ID, acc_date = user_data
    formatter('account data', user_time)
    age_data, age_time = perf_counter(daily_readme, datetime.datetime(2001, 4, 21))
    formatter('age calculation', age_time)
    total_loc, loc_time = perf_counter(loc_query, ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'], 7)
    formatter('LOC (cached)', loc_time) if total_loc[-1] else formatter('LOC (no cache)', loc_time)

    commit_result, commit_time = perf_counter(graph_commits)
    commit_data, year_commits = commit_result
    rank_data, rank_time = perf_counter(committers_rank_getter, USER_NAME)
    repo_data, repo_time = perf_counter(graph_repos_stars, 'repos', ['OWNER'])
    contrib_data, contrib_time = perf_counter(graph_repos_stars, 'repos',
                                              ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])
    follower_data, follower_time = perf_counter(follower_getter, USER_NAME)
    top_langs, lang_time = perf_counter(top_languages_getter, USER_NAME)
    streak_stats, streak_time = perf_counter(get_streak_stats)

    for index in range(len(total_loc) - 1): total_loc[index] = '{:,}'.format(total_loc[index])

    svg_overwrite('dark_mode.svg', age_data, commit_data, year_commits, rank_data, repo_data, contrib_data, follower_data,
                  total_loc[:-1], top_langs, streak_stats)
    svg_overwrite('light_mode.svg', age_data, commit_data, year_commits, rank_data, repo_data, contrib_data, follower_data,
                  total_loc[:-1], top_langs, streak_stats)

    print('\033[F\033[F\033[F\033[F\033[F\033[F\033[F\033[F',
          '{:<21}'.format('Total function time:'),
          '{:>11}'.format(
              '%.4f' % (user_time + age_time + loc_time + commit_time + rank_time + repo_time + contrib_time)),
          ' s \033[E\033[E\033[E\033[E\033[E\033[E\033[E\033[E', sep='')

    print('Total GitHub GraphQL API calls:', '{:>3}'.format(sum(QUERY_COUNT.values())))
    for funct_name, count in QUERY_COUNT.items(): print('{:<28}'.format('   ' + funct_name + ':'),
                                                        '{:>6}'.format(count))
