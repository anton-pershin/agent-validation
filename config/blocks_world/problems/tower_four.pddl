(define (problem tower-four)
  (:domain blocks-world)
  (:objects a b c d - block)
  (:init
    (on-table a) (on-table b) (on-table c) (on-table d)
    (clear a) (clear b) (clear c) (clear d) (hand-empty))
  (:goal (and (on a b) (on b c) (on c d)))
)
