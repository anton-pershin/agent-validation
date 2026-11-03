(define (problem stack-three)
  (:domain blocks-world)
  (:objects a b c - block)
  (:init
    (on-table a) (on-table b) (on-table c)
    (clear a) (clear b) (clear c) (hand-empty))
  (:goal (and (on a b) (on b c)))
)
