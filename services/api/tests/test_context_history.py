"""Long-history retrieval remains bounded, chronological and tenant-scoped."""

from sqlalchemy import Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from sixsentences_server.core.context_history import load_context_rows


class _Base(DeclarativeBase):
    pass


class _Message(_Base):
    __tablename__ = "context_history_test_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer)
    resource_id: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(String)


def test_retrieval_preserves_old_anchors_and_never_crosses_resource_or_org() -> None:
    engine = create_engine("sqlite://")
    _Base.metadata.create_all(engine)
    with Session(engine) as session:
        messages = [
            _Message(org_id=1, resource_id=1, role="user", content="Initial research goal"),
            *[
                _Message(org_id=1, resource_id=1, role="user", content=f"Early filler {index}")
                for index in range(8)
            ],
            _Message(
                org_id=1,
                resource_id=1,
                role="user",
                content="Always keep original measurements",
            ),
            _Message(org_id=1, resource_id=1, role="user", content="Zephyr baseline is 42"),
            *[
                _Message(org_id=1, resource_id=1, role=role, content=f"Turn {index}")
                for index in range(400)
                for role in ("user", "assistant")
            ],
            _Message(org_id=2, resource_id=1, role="user", content="Foreign tenant Zephyr"),
            _Message(org_id=1, resource_id=2, role="user", content="Foreign resource Zephyr"),
        ]
        session.add_all(messages)
        session.flush()
        rows = load_context_rows(
            session,
            _Message,
            resource_column=_Message.resource_id,
            resource_id=1,
            org_id=1,
            current_request="Explain Zephyr",
        )
        contents = [row.content for row in rows]
        assert "Initial research goal" in contents
        assert "Always keep original measurements" in contents
        assert "Zephyr baseline is 42" in contents
        assert all(row.org_id == row.resource_id == 1 for row in rows)
        assert len(rows) <= 460
        assert [row.id for row in rows] == sorted({row.id for row in rows}, reverse=True)
    engine.dispose()
