"""A closeable iterator owning a document reservation, including before start."""


class ReservedComputation:
    def __init__(self, document, steps):
        self.document, self.steps = document, iter(steps)
        self.closed = False
        document.history.reserve(self)

    def __iter__(self):
        return self

    def __next__(self):
        if self.closed:
            raise StopIteration
        try:
            return next(self.steps)
        except BaseException:
            self.close()
            raise

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                close = getattr(self.steps, "close", None)
                if close is not None:
                    close()
            finally:
                self.document.history.release(self)


def complete(steps):
    """Keep synchronous APIs on the exact same incremental evaluation path."""
    try:
        while True:
            next(steps)
    except StopIteration as result:
        return result.value
    finally:
        close = getattr(steps, "close", None)
        if close is not None:
            close()
