from copy import copy
import sys
import re


class DocoptError(Exception):

    """Error in construction of usage-message by developer."""


class DocoptExit(SystemExit):

    """Exit in case user invoked program with incorrect arguments."""

    usage = ''

    def __init__(self, message=''):
        SystemExit.__init__(self, (message + '\n' + self.usage).strip())


class Pattern(object):

    def __init__(self, *children):
        self.children = list(children)

    def __eq__(self, other):
        return repr(self) == repr(other)

    def __hash__(self):
        return hash(repr(self))

    def __repr__(self):
        return '%s(%s)' % (self.__class__.__name__,
                           ', '.join(repr(a) for a in self.children))

    @property
    def flat(self):
        if not hasattr(self, 'children'):
            return [self]
        return sum([c.flat for c in self.children], [])

    def fix(self):
        self.fix_identities()
        self.fix_list_arguments()
        return self

    def fix_identities(self, uniq=None):
        """Make pattern-tree tips point to same object if they are equal."""
        if not hasattr(self, 'children'):
            return self
        uniq = list(set(self.flat)) if uniq == None else uniq
        for i, c in enumerate(self.children):
            if not hasattr(c, 'children'):
                assert c in uniq
                self.children[i] = uniq[uniq.index(c)]
            else:
                c.fix_identities(uniq)

    def fix_list_arguments(self):
        """Find arguments that should accumulate values and fix them."""
        either = [list(c.children) for c in self.either.children]
        for case in either:
            case = [c for c in case if case.count(c) > 1]
            for a in [e for e in case if type(e) == Argument]:
                a.value = []
        return self

    @property
    def either(self):
        """Transform pattern into an equivalent, with only top-level Either."""
        # Currently the pattern will not be equivalent, but more "narrow",
        # although good enough to reason about list arguments.
        if not hasattr(self, 'children'):
            return Either(Required(self))
        else:
            ret = []
            groups = [[self]]
            while groups:
                children = groups.pop(0)
                types = [type(c) for c in children]
                if Either in types:
                    either = [c for c in children if type(c) is Either][0]
                    children.pop(children.index(either))
                    for c in either.children:
                        groups.append([c] + children)
                elif Required in types:
                    required = [c for c in children if type(c) is Required][0]
                    children.pop(children.index(required))
                    groups.append(list(required.children) + children)
                elif Optional in types:
                    optional = [c for c in children if type(c) is Optional][0]
                    children.pop(children.index(optional))
                    groups.append(list(optional.children) + children)
                elif OneOrMore in types:
                    oneormore = [c for c in children if type(c) is OneOrMore][0]
                    children.pop(children.index(oneormore))
                    groups.append(list(oneormore.children) * 2 + children)
                else:
                    ret.append(children)
            return Either(*[Required(*e) for e in ret])


class Argument(Pattern):

    def __init__(self, name, value=None):
        self.name = name
        self.value = value

    def match(self, left, collected=None):
        collected = [] if collected is None else collected
        args = [l for l in left if type(l) is Argument]
        if not len(args):
            return False, left, collected
        left.remove(args[0])
        if type(self.value) is not list:
            return True, left, collected + [Argument(self.name, args[0].value)]
        same_name = [a for a in collected
                     if type(a) is Argument and a.name == self.name]
        if len(same_name):
            same_name[0].value += [args[0].value]
            return True, left, collected
        else:
            return True, left, collected + [Argument(self.name,
                                                     [args[0].value])]

    def __repr__(self):
        return 'Argument(%r, %r)' % (self.name, self.value)


class Command(Pattern):

    def __init__(self, name, value=False):
        self.name = name
        self.value = value

    def match(self, left, collected=None):
        collected = [] if collected is None else collected
        args = [l for l in left if type(l) is Argument]
        if not len(args) or args[0].value != self.name:
            return False, left, collected
        left.remove(args[0])
        return True, left, collected + [Command(self.name, True)]

    def __repr__(self):
        return 'Command(%r, %r)' % (self.name, self.value)


class Option(Pattern):

    def __init__(self, short=None, long=None, argcount=0, value=False):
        assert argcount in (0, 1)
        self.short, self.long = short, long
        self.argcount, self.value = argcount, value

    def match(self, left, collected=None):
        collected = [] if collected is None else collected
        left_ = []
        for l in left:
            # if this is so greedy, how to handle OneOrMore then?
            if not (type(l) is Option and
                    (self.short, self.long) == (l.short, l.long)):
                left_.append(l)
        return (left != left_), left_, collected

    @property
    def name(self):
        return self.long or self.short

    def __repr__(self):
        return 'Option(%r, %r, %r, %r)' % (self.short, self.long,
                                           self.argcount, self.value)


class AnyOptions(Pattern):

    def match(self, left, collected=None):
        collected = [] if collected is None else collected
        left_ = [l for l in left if not type(l) == Option]
        return (left != left_), left_, collected


class Required(Pattern):

    def match(self, left, collected=None):
        collected = [] if collected is None else collected
        l = copy(left)
        c = copy(collected)
        for p in self.children:
            matched, l, c = p.match(l, c)
            if not matched:
                return False, left, collected
        return True, l, c


class Optional(Pattern):

    def match(self, left, collected=None):
        collected = [] if collected is None else collected
        left = copy(left)
        for p in self.children:
            m, left, collected = p.match(left, collected)
        return True, left, collected


class OneOrMore(Pattern):

    def match(self, left, collected=None):
        assert len(self.children) == 1
        collected = [] if collected is None else collected
        l = copy(left)
        c = copy(collected)
        l_ = None
        matched = True
        times = 0
        while matched:
            # could it be that something didn't match but changed l or c?
            matched, l, c = self.children[0].match(l, c)
            times += 1 if matched else 0
            if l_ == l:
                break
            l_ = copy(l)
        if times >= 1:
            return True, l, c
        return False, left, collected


class Either(Pattern):

    def match(self, left, collected=None):
        collected = [] if collected is None else collected
        outcomes = []
        for p in self.children:
            matched, _, _ = outcome = p.match(copy(left), copy(collected))
            if matched:
                outcomes.append(outcome)
        if outcomes:
            return min(outcomes, key=lambda outcome: len(outcome[1]))
        return False, left, collected


def option(full_description):
    short, long, argcount, value = None, None, 0, False
    options, _, description = full_description.strip().partition('  ')
    options = options.replace(',', ' ').replace('=', ' ')
    for s in options.split():
        if s.startswith('--'):
            long = s
        elif s.startswith('-'):
            short = s
        else:
            argcount = 1
    if argcount:
        matched = re.findall('\[default: (.*)\]', description, flags=re.I)
        value = matched[0] if matched else False
    return Option(short, long, argcount, value)


class TokenStream(object):

    def __init__(self, source):
        self.s = source.split() if type(source) is str else source

    def __iter__(self):
        return iter(self.s)

    def move(self, default=None):
        return self.s.pop(0) if len(self.s) else default

    def current(self, default=None):
        return self.s[0] if len(self.s) else default


def parse_long(raw, options, tokens, is_pattern):
    try:
        i = raw.index('=')
        raw, value = raw[:i], raw[i + 1:]
    except ValueError:
        value = None
    opt = [o for o in options if o.long and o.long.lstrip('-').startswith(raw)]
    if len(opt) < 1:
        if is_pattern:
            raise DocoptError('--%s in "usage" should be '
                              'mentioned in option-description' % raw)
        raise DocoptExit('--%s is not recognized' % raw)
    if len(opt) > 1:
        if is_pattern:
            raise DocoptError('--%s in "usage" is not a unique prefix: %s?' %
                              (raw, ', '.join('--%s' % o.long for o in opt)))
        raise DocoptExit('--%s is not a unique prefix: %s?' %
                         (raw, ', '.join('--%s' % o.long for o in opt)))
    opt = copy(opt[0])
    if opt.argcount == 1:
        if value is None:
            if tokens.current() is None:
                if is_pattern:
                    raise DocoptError('--%s in "usage" requires argument' %
                                      opt.name)
                raise DocoptExit('--%s requires argument' % opt.name)
            value = tokens.move()
    elif value is not None:
        if is_pattern:
            raise DocoptError('--%s in "usage" must not have an argument' %
                             opt.name)
        raise DocoptExit('--%s must not have an argument' % opt.name)
    opt.value = value or True
    return opt


def parse_shorts(raw, options, tokens, is_pattern):
    parsed = []
    while raw != '':
        opt = [o for o in options
               if o.short and o.short.lstrip('-').startswith(raw[0])]
        if len(opt) > 1:
            raise DocoptError('-%s is specified ambiguously %d times' %
                              (raw[0], len(opt)))
        if len(opt) < 1:
            if is_pattern:
                raise DocoptError('-%s in "usage" should be mentioned '
                                  'in option-description' % raw[0])
            raise DocoptExit('-%s is not recognized' % raw[0])
        assert len(opt) == 1
        opt = copy(opt[0])
        raw = raw[1:]
        if opt.argcount == 0:
            value = True
        else:
            if raw == '':
                if tokens.current() is None:
                    if is_pattern:
                        raise DocoptError('-%s in "usage" requires argument' %
                                          opt.short[0])
                    raise DocoptExit('-%s requires argument' % opt.short[0])
                raw = tokens.move()
            value, raw = raw, ''
        opt.value = value
        parsed += [opt]
    return parsed


def parse_pattern(source, options):
    tokens = TokenStream(re.sub(r'([\[\]\(\)\|]|\.\.\.)', r' \1 ', source))
    result = parse_expr(tokens, options)
    assert tokens.current() is None
    return Required(*result)


def parse_expr(tokens, options):
    """expr ::= seq , ( '|' seq )* ;"""
    seq = parse_seq(tokens, options)

    if tokens.current() != '|':
        return seq

    if len(seq) > 1:
        seq = [Required(*seq)]
    result = seq
    while tokens.current() == '|':
        tokens.move()
        seq = parse_seq(tokens, options)
        result += [Required(*seq)] if len(seq) > 1 else seq

    return result if len(result) == 1 else [Either(*result)]


def parse_seq(tokens, options):
    """seq ::= ( atom [ '...' ] )* ;"""
    result = []
    while tokens.current() not in [None, ']', ')', '|']:
        atom = parse_atom(tokens, options)
        if tokens.current() == '...':
            atom = [OneOrMore(*atom)]
            tokens.move()
        result += atom
    return result


def parse_atom(tokens, options):
    """atom ::= '(' expr ')' | '[' expr ']' | '[options]'
            | long | shorts | argument | command ;
    """
    token = tokens.move()
    result = []
    if token == '(':
        result = [Required(*parse_expr(tokens, options))]
        if tokens.move() != ')':
            raise DocoptError("Unmatched '('")
        return result
    elif token == '[':
        if tokens.current() == 'options':
            result = [Optional(AnyOptions())]
            tokens.move()
        else:
            result = [Optional(*parse_expr(tokens, options))]
        if tokens.move() != ']':
            raise DocoptError("Unmatched '['")
        return result
    elif token == '--':
        return []  # allow "usage: prog [-o] [--] <arg>"
    elif token.startswith('--'):
        return [parse_long(token[2:], options, tokens, is_pattern=True)]
    elif token.startswith('-'):
        return parse_shorts(token[1:], options, tokens, is_pattern=True)
    elif token.startswith('<') and token.endswith('>') or token.isupper():
        return [Argument(token)]
    else:
        return [Command(token)]


def parse_args(source, options):
    tokens = TokenStream(source)
    options = copy(options)
    parsed = []
    while tokens.current() is not None:
        token = tokens.move()
        if token == '--':
            parsed += [Argument(None, v) for v in tokens]
            break
        elif token.startswith('--'):
            parsed += [parse_long(token[2:], options, tokens, is_pattern=False)]
        elif token.startswith('-') and token != '-':
            parsed += parse_shorts(token[1:], options, tokens, is_pattern=False)
        else:
            parsed.append(Argument(None, token))
    return parsed


def parse_doc_options(doc):
    return [option('-' + s) for s in re.split('^ *-|\n *-', doc)[1:]]


def printable_usage(doc):
    return re.split(r'\n\s*\n',
            ''.join(re.split(r'([Uu][Ss][Aa][Gg][Ee]:)', doc)[1:]))[0].strip()


def formal_usage(printable_usage):
    pu = printable_usage.split()[1:]  # split and drop "usage:"
    return ' '.join('|' if s == pu[0] else s for s in pu[1:])


def extras(help, version, options, doc):
    if help and any((o.name in ('-h', '--help')) and o.value for o in options):
        print(doc.strip())
        exit()
    if version and any(o.long == '--version' and o.value for o in options):
        print(version)
        exit()


class Dict(dict):
    def __repr__(self):
        return '{%s}' % ',\n '.join('%r: %r' % i for i in sorted(self.items()))


def docopt(doc, argv=sys.argv[1:], help=True, version=None):
    DocoptExit.usage = docopt.usage = usage = printable_usage(doc)
    pot_options = parse_doc_options(doc)
    argv = parse_args(argv, options=pot_options)
    options = [o for o in argv if type(o) is Option]
    extras(help, version, options, doc)
    formal_pattern = parse_pattern(formal_usage(usage), options=pot_options)
    pot_arguments = [a for a in formal_pattern.flat
                     if type(a) in [Argument, Command]]
    matched, left, arguments = formal_pattern.fix().match(argv)
    if matched and left == []:  # better message if left?
        return Dict((a.name, a.value) for a in
                    (pot_options + options + pot_arguments + arguments))
    raise DocoptExit()
