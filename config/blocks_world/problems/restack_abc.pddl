(define (problem restack-abc)
  (:domain blocks-world)
  (:objects a b c - block)
  (:init
    (on a b) (on-table b) (on-table c)
    (clear a) (clear c) (hand-empty))
  (:goal (and (on-table a) (on b c)))
)
